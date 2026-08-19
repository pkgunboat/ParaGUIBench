# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0
"""
Doubao Seed GUI Agent - 基于OSWorld官方实现
使用volcenginesdkarkruntime.Ark SDK和特殊的XML格式prompt
"""
import os
import sys
import re
import base64
import io
import logging
import time
from typing import Optional, Dict, List, Tuple, Union, Any
from PIL import Image
from loguru import logger

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 导入XML解析器（本地实现）和action转换函数（ui_tars官方）
from utils.xml_action_parser import parse_xml_action_v3
from ui_tars.action_parser import parsing_response_to_pyautogui_code

# 官方 system prompt（简短版）
SYSTEM_PROMPT = "You are provided with a task description, a history of previous actions, and corresponding screenshots. Your goal is to perform the next action to complete the task. Please note that if performing the same action multiple times results in a static screen with no changes, you should attempt a modified or alternative action.\n\nIf the screen is locked or the display has turned off, unlock it using the lock screen password: passoword (8 letters).\n\nIMPORTANT - COORDINATE SYSTEM:\nThe screenshot resolution is 1920x1080 pixels. When specifying coordinates:\n- Use NORMALIZED coordinates in the range 0-1000 (NOT pixel coordinates)\n- For example, top-left corner is <point>0 0</point>, center is <point>500 500</point>, bottom-right is <point>1000 1000</point>\n- To click at pixel (960, 540), use <point>500 500</point> (which is 960/1920*1000, 540/1080*1000)\n\nIf your task contains a [Global Task Context] section, it provides background information. Focus ONLY on the sub-task described BEFORE that section. Use data from the global context (names, addresses, URLs, etc.) when the sub-task references them."

# 官方 Function Definition（完整的 XML 格式说明）
FUNCTION_DEFINITION = '''## Function Definition

CRITICAL: You MUST output the COMPLETE XML format including ALL wrapper tags. DO NOT output simplified or shortened formats.

COORDINATE SYSTEM:
- Use NORMALIZED coordinates in range 0-1000 (NOT pixel values)
- Screen is 1920x1080 pixels
- Top-left corner: <point>0 0</point>
- Center: <point>500 500</point>  
- Bottom-right: <point>1000 1000</point>
- Example: To click at pixel position (96, 54), calculate: (96/1920*1000, 54/1080*1000) = <point>50 50</point>

- You have access to the following functions:
{"type": "function", "name": "call_user", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to request their input, feedback, or guidance."}}, "required": []}, "description": "This function is used to interact with the user by displaying a message and requesting their input, feedback, or guidance."}
{"type": "function", "name": "click", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left single click action."}
{"type": "function", "name": "drag", "parameters": {"type": "object", "properties": {"start_point": {"type": "string", "description": "Drag start point. The format is: <point>x y</point>"}, "end_point": {"type": "string", "description": "Drag end point. The format is: <point>x y</point>"}}, "required": ["start_point", "end_point"]}, "description": "Mouse left button drag action."}
{"type": "function", "name": "finished", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Provide the final answer or response to complete the task."}}, "required": []}, "description": "This function is used to indicate the completion of a task by providing the final answer or response."}
{"type": "function", "name": "hotkey", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Hotkeys you want to press. Split keys with a space and use lowercase."}}, "required": ["key"]}, "description": "Press hotkey."}
{"type": "function", "function": {"name": "infeasible", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to explain why the current task is infeasible."}}, "required": ["content"]}, "description": "This function is used to indicate that the current task is infeasible thus agent ends the task."}
{"type": "function", "name": "left_double", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left double click action."}
{"type": "function", "name": "right_single", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse right single click action."}
{"type": "function", "name": "scroll", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Scroll start position. If not specified, default to execute on the current mouse position. The format is: <point>x y</point>"}, "direction": {"type": "string", "description": "Scroll direction.", "enum": ["up", "down", "left", "right"]}}, "required": ["direction", "point"]}, "description": "Scroll action."}
{"type": "function", "name": "type", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Type content. If you want to submit your input, use \\n at the end of content."}}, "required": ["content"]}, "description": "Type content."}
{"type": "function", "name": "wait", "parameters": {"type": "object", "properties": {"time": {"type": "integer", "description": "Wait time in seconds."}}, "required": []}, "description": "Wait for a while."}

- To call a function, use the following structure without any suffix:

<think_never_used_51bce0c785ca2f68081bfa7d91973934> reasoning process </think_never_used_51bce0c785ca2f68081bfa7d91973934>
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=example_function_name><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_1>value_1</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

## Important Notes
- Function calls must begin with <function_never_used_51bce0c785ca2f68081bfa7d91973934= and end with </function_never_used_51bce0c785ca2f68081bfa7d91973934>.
- All required parameters must be explicitly provided.

## Additional Notes
- You can execute multiple actions within a single tool call. For example:
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=example_function_1><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_1>value_1</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=example_function_2><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_3>value_4</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>
- 当你判断任务请求是无法执行的时候，你应该调用Infeasible工具结束任务并解释原因。
        判断标准：当一个请求符合以下任何一条标准时，应被归类为"无法执行"。
        1. 技术/物理层面的矛盾： 指令本身包含逻辑上或物理上无法实现的要求。
        2. 工具/功能错配： 指令要求在一个软件中执行另一个软件的功能，或者执行该软件根本不具备的功能。
        3. 超出操作边界/范围： 指令要求执行的操作超出了当前用户会话、权限或应用程序的逻辑边界，涉及未告知的隐私信息或者未授权的操作。
        4. 依赖隐性知识或外部条件： 任务的完成依赖于Agent无法获取的外部硬件、物理环境、未声明的插件/扩展、或特定的文件/数据。

        输出指令：
        如果请求被判断为"无法执行"，你应该向用户解释为什么这个任务超出了你的能力范围（例如，指出它需要直接操作某个硬件），并尽可能提供一个指导性的替代方案，让用户可以自己完成该任务。
        你应该非常非常谨慎地使用Infeasible工具，因为它会直接结束任务并降低用户体验。所以非必要的时候，你不应该调用Infeasible工具，尽量以finish工具结束任务并向用户提示原因就好。

## CRITICAL REMINDER - OUTPUT FORMAT REQUIREMENTS
YOU MUST OUTPUT THE COMPLETE XML FORMAT WITH ALL TAGS. Examples:

CORRECT FORMAT (with all wrapper tags):
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=click><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=point><point>18 59</point></parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

WRONG FORMAT (simplified - DO NOT USE):
click>point><point>18 59</point>

YOU MUST INCLUDE:
1. Opening tag: <seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>
2. Function tag: <function_never_used_51bce0c785ca2f68081bfa7d91973934=FUNCTION_NAME>
3. Parameter tags: <parameter_never_used_51bce0c785ca2f68081bfa7d91973934=PARAM_NAME>VALUE</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>
4. Closing function tag: </function_never_used_51bce0c785ca2f68081bfa7d91973934>
5. Closing tool_call tag: </seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

DO NOT output simplified formats or skip any tags!'''

# 尝试导入Ark SDK
try:
    from volcenginesdkarkruntime import Ark
    ARK_AVAILABLE = True
except ImportError:
    ARK_AVAILABLE = False
    logger.warning("volcenginesdkarkruntime not installed. Doubao Seed Agent will not work.")

# 终止词
FINISH_WORD = "finished"
WAIT_WORD = "wait"
ENV_FAIL_WORD = "error_env"
CALL_USER = "call_user"
INFEASIBLE = "infeasible"

# GUI工具定义（与官方实现一致）
GUI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "string",
                        "description": "Click coordinates. The format is: <point>x y</point>"
                    }
                },
                "required": ["point"]
            },
            "description": "Mouse left single click action."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "left_double",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "string",
                        "description": "Click coordinates. The format is: <point>x y</point>"
                    }
                },
                "required": ["point"]
            },
            "description": "Mouse left double click action."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "right_single",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "string",
                        "description": "Click coordinates. The format is: <point>x y</point>"
                    }
                },
                "required": ["point"]
            },
            "description": "Mouse right single click action."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_point": {
                        "type": "string",
                        "description": "Drag start point. The format is: <point>x y</point>"
                    },
                    "end_point": {
                        "type": "string",
                        "description": "Drag end point. The format is: <point>x y</point>"
                    }
                },
                "required": ["start_point", "end_point"]
            },
            "description": "Mouse left button drag action."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "string",
                        "description": "Scroll start position. The format is: <point>x y</point>"
                    },
                    "direction": {
                        "type": "string",
                        "description": "Scroll direction.",
                        "enum": ["up", "down", "left", "right"]
                    }
                },
                "required": ["direction"]
            },
            "description": "Scroll action."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Type content. If you want to submit your input, use \\n at the end of content."
                    }
                },
                "required": ["content"]
            },
            "description": "Type content."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Hotkeys you want to press. Split keys with a space and use lowercase."
                    }
                },
                "required": ["key"]
            },
            "description": "Press hotkey."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finished",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Provide the final answer or response to complete the task."
                    }
                },
                "required": []
            },
            "description": "This function is used to indicate the completion of a task by providing the final answer or response."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "call_user",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Message or information displayed to the user to request their input, feedback, or guidance."
                    }
                },
                "required": []
            },
            "description": "This function is used to interact with the user by displaying a message and requesting their input, feedback, or guidance."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "integer",
                        "description": "Wait time in seconds."
                    }
                },
                "required": []
            },
            "description": "Wait for a while."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "infeasible",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Message or information displayed to the user to explain why the current task is infeasible."
                    }
                },
                "required": ["content"]
            },
            "description": "This function is used to indicate that the current task is infeasible thus agent ends the task."
        }
    }
]

# System prompt和function calling instructions现在从prompts模块导入
# 使用 DOUBAO_SEED_SYSTEM_PROMPT 和 DOUBAO_FUNCTION_CALLING_INSTRUCTIONS

# 保留旧的 function calling instructions 供参考
OLD_FUNCTION_CALLING_INSTRUCTIONS = '''## Function Definition

- You have access to the following functions:
{"type": "function", "name": "call_user", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to request their input, feedback, or guidance."}}, "required": []}, "description": "This function is used to interact with the user by displaying a message and requesting their input, feedback, or guidance."}
{"type": "function", "name": "click", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left single click action."}
{"type": "function", "name": "drag", "parameters": {"type": "object", "properties": {"start_point": {"type": "string", "description": "Drag start point. The format is: <point>x y</point>"}, "end_point": {"type": "string", "description": "Drag end point. The format is: <point>x y</point>"}}, "required": ["start_point", "end_point"]}, "description": "Mouse left button drag action."}
{"type": "function", "name": "finished", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Provide the final answer or response to complete the task."}}, "required": []}, "description": "This function is used to indicate the completion of a task by providing the final answer or response."}
{"type": "function", "name": "hotkey", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Hotkeys you want to press. Split keys with a space and use lowercase."}}, "required": ["key"]}, "description": "Press hotkey."}
{"type": "function", "function": {"name": "infeasible", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to explain why the current task is infeasible."}}, "required": ["content"]}, "description": "This function is used to indicate that the current task is infeasible thus agent ends the task."}
{"type": "function", "name": "left_double", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left double click action."}
{"type": "function", "name": "right_single", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse right single click action."}
{"type": "function", "name": "scroll", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Scroll start position. If not specified, default to execute on the current mouse position. The format is: <point>x y</point>"}, "direction": {"type": "string", "description": "Scroll direction.", "enum": ["up", "down", "left", "right"]}}, "required": ["direction", "point"]}, "description": "Scroll action."}
{"type": "function", "name": "type", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Type content. If you want to submit your input, use \\n at the end of content."}}, "required": ["content"]}, "description": "Type content."}
{"type": "function", "name": "wait", "parameters": {"type": "object", "properties": {"time": {"type": "integer", "description": "Wait time in seconds."}}, "required": []}, "description": "Wait for a while."}


## CRITICAL REMINDER
You MUST output your action using the XML format above. DO NOT just return thinking text without a tool call.
Example of CORRECT output:
<think_never_used_51bce0c785ca2f68081bfa7d91973934>
I need to click the Chrome icon at the top left to open the browser.
</think_never_used_51bce0c785ca2f68081bfa7d91973934>
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=click><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=point><point>50 50</point></parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

Example of WRONG output (thinking only, no tool call):
<think_never_used_51bce0c785ca2f68081bfa7d91973934>
I should click the Chrome icon...
</think_never_used_51bce0c785ca2f68081bfa7d91973934>
[This is WRONG - you must include the tool call!]
- To call a function, use the following structure without any suffix:

<think_never_used_51bce0c785ca2f68081bfa7d91973934> reasoning process </think_never_used_51bce0c785ca2f68081bfa7d91973934>
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=example_function_name><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_1>value_1</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

## Important Notes
- Function calls must begin with <function_never_used_51bce0c785ca2f68081bfa7d91973934= and end with </function_never_used_51bce0c785ca2f68081bfa7d91973934>.
- All required parameters must be explicitly provided.

## Additional Notes
- You can execute multiple actions within a single tool call.
- 当你判断任务请求是无法执行的时候，你应该调用Infeasible工具结束任务并解释原因。'''


class DoubaoSeedGUIAgent:
    """
    Doubao Seed GUI Agent - 基于OSWorld官方实现
    """
    
    def __init__(
        self,
        platform="ubuntu",
        model_type="doubao",
        max_trajectory_length=50,
        history_n=3,  # 历史图片数量
        runtime_conf: dict = None,
        # 图片resize设置
        resize_image=False,
        resized_image_width=1920,
        resized_image_height=1080,
        controller: Optional[Any] = None,
        execute_actions: bool = False,
    ):
        """
        初始化Doubao Seed GUI Agent
        
        Args:
            platform: 平台类型，默认ubuntu
            model_type: 模型类型标识
            max_trajectory_length: 最大轨迹长度
            history_n: 保留的历史图片数量
            runtime_conf: 运行时配置，包含api_key, base_url, model_name等
            resize_image: 是否resize图片
            resized_image_width: resize后的宽度
            resized_image_height: resize后的高度
            controller: GUI 执行器（PythonController），用于在 predict 内部执行动作
            execute_actions: 是否在 predict 内部执行 GUI 动作
        """
        if not ARK_AVAILABLE:
            raise ImportError("volcenginesdkarkruntime is required for Doubao Seed Agent")
        
        self.platform = platform
        self.model_type = model_type
        self.max_trajectory_length = max_trajectory_length
        self.history_n = history_n
        self.resize_image = resize_image
        self.resized_image_width = resized_image_width
        self.resized_image_height = resized_image_height
        self.controller = controller
        self.execute_actions = execute_actions
        self.last_round_timing: Dict[str, Any] = {}
        self.last_raw_content: str = ""  # 保存最近一次 API 调用的原始 content，用于写入执行记录
        self.last_token_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 运行时配置
        runtime_conf = runtime_conf or {}
        self.api_key = runtime_conf.get('doubao_api_key') or os.environ.get('DOUBAO_API_KEY')
        self.base_url = runtime_conf.get('doubao_base_url') or os.environ.get('DOUBAO_API_URL')
        self.model_name = runtime_conf.get('doubao_model_name', 'doubao-seed-1-8-251228')
        self.max_tokens = runtime_conf.get('max_tokens', 4096)
        self.temperature = runtime_conf.get('temperature', 0.3)
        self.top_p = runtime_conf.get('top_p', 0.95)
        
        # 初始化Ark客户端
        self.ark_client = Ark(
            base_url=self.base_url,
            api_key=self.api_key
        )
        
        # 历史记录
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_images = []
        self.history_responses = []
        
        self.logger = logger
        
    def reset(self, _logger=None, vm_ip=None, **kwargs):
        """重置agent状态"""
        if _logger is not None:
            self.logger = _logger
        
        self.vm_ip = vm_ip
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_images = []
        self.history_responses = []
        self.last_round_timing = {}
        self.last_raw_content = ""

    def _execute_actions(self, actions: List[str]) -> Dict[str, Any]:
        """
        在 agent 内部执行 GUI 动作，用于与 Claude 执行路径对齐
        
        Args:
            actions: 由模型解析得到的 pyautogui 代码列表
        
        Returns:
            Dict[str, Any]: 执行结果摘要
        """
        result_summary = {
            "executed": 0,
            "failed": 0,
            "errors": []
        }
        
        if not self.controller:
            result_summary["errors"].append("controller is not set")
            result_summary["failed"] = len(actions)
            return result_summary
        
        for action_code in actions:
            try:
                exec_result = self.controller.execute_python_command(action_code)
                if not exec_result or exec_result.get("status") != "success":
                    result_summary["failed"] += 1
                    result_summary["errors"].append(str(exec_result))
                else:
                    result_summary["executed"] += 1
            except Exception as e:
                result_summary["failed"] += 1
                result_summary["errors"].append(str(e))
        
        return result_summary
    
    def _prepare_screenshot(self, screenshot_bytes):
        """准备screenshot为base64格式"""
        if isinstance(screenshot_bytes, bytes):
            image = Image.open(io.BytesIO(screenshot_bytes))
        else:
            image = screenshot_bytes
            
        width, height = image.size
        
        if self.resize_image:
            resized_image = image.resize(
                (self.resized_image_width, self.resized_image_height)
            )
            image_bytes_io = io.BytesIO()
            resized_image.save(image_bytes_io, format="PNG")
            image_bytes = image_bytes_io.getvalue()
            screenshot_b64 = base64.b64encode(image_bytes).decode('utf-8')
        else:
            if isinstance(screenshot_bytes, bytes):
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            else:
                image_bytes_io = io.BytesIO()
                image.save(image_bytes_io, format="PNG")
                screenshot_b64 = base64.b64encode(image_bytes_io.getvalue()).decode('utf-8')
        
        return screenshot_b64, width, height
    
    def _expand_simplified_xml(self, content: str) -> str:
        """
        将简化的XML格式补全成完整格式
        
        简化格式示例：
          click>point><point>18 59</point>
        
        完整格式示例：
          <seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>
          <function_never_used_51bce0c785ca2f68081bfa7d91973934=click>
          <parameter_never_used_51bce0c785ca2f68081bfa7d91973934=point>
          <point>18 59</point>
          </parameter_never_used_51bce0c785ca2f68081bfa7d91973934>
          </function_never_used_51bce0c785ca2f68081bfa7d91973934>
          </seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>
        """
        # 如果已经是完整格式，直接返回
        if "seed:tool_call_never_used" in content:
            return content
        
        # 检测是否是简化格式（包含 > 但不包含完整的XML标签）
        if ">" not in content or "<function_never_used" in content:
            return content
        
        self.logger.debug(f"[_expand_simplified_xml] 检测到简化格式，开始补全")
        self.logger.debug(f"[_expand_simplified_xml] 原始内容: {content}")
        
        # 使用正则提取函数名和参数
        # 格式: function_name>param_name>...value...</param>...
        import re
        
        # 匹配模式：function_name>param1_name>value</param1>param2_name>value</param2>...
        # 先找第一个 > 之前的部分作为函数名
        match = re.match(r'([a-z_]+)>(.*)', content, re.DOTALL)
        if not match:
            self.logger.warning(f"[_expand_simplified_xml] 无法解析简化格式")
            return content
        
        function_name = match.group(1)
        params_part = match.group(2)
        
        self.logger.debug(f"[_expand_simplified_xml] 函数名: {function_name}")
        self.logger.debug(f"[_expand_simplified_xml] 参数部分: {params_part}")
        
        # 构建完整的XML
        expanded = f"<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
        expanded += f"<function_never_used_51bce0c785ca2f68081bfa7d91973934={function_name}>"
        
        # 解析参数部分，支持两种格式：
        # 1. 带XML标签：param_name><tag>value</tag>  (例如：point><point>18 59</point>)
        # 2. 纯文本：param_name>plain_text  (例如：content>Beijing current temperature)
        
        # 先尝试匹配带XML标签的参数
        param_with_tags_pattern = r'([a-z_]+)>(<[^>]+>.*?</[^>]+>)'
        params_with_tags = re.findall(param_with_tags_pattern, params_part, re.DOTALL)
        
        if params_with_tags:
            # 找到了带XML标签的参数
            for param_name, param_value in params_with_tags:
                self.logger.debug(f"[_expand_simplified_xml]   参数(XML): {param_name} = {param_value}")
                expanded += f"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={param_name}>"
                expanded += param_value
                expanded += f"</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
        else:
            # 没有XML标签，尝试匹配纯文本参数：param_name>value
            # 按 > 分割，第一个是参数名，其余是值
            parts = params_part.split('>', 1)
            if len(parts) == 2:
                param_name = parts[0]
                param_value = parts[1].strip()
                self.logger.debug(f"[_expand_simplified_xml]   参数(纯文本): {param_name} = {param_value}")
                expanded += f"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={param_name}>"
                expanded += param_value
                expanded += f"</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
        
        expanded += f"</function_never_used_51bce0c785ca2f68081bfa7d91973934>"
        expanded += f"</seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
        
        self.logger.debug(f"[_expand_simplified_xml] 补全后内容: {expanded}")
        
        return expanded
    
    def _call_ark_api(self, messages):
        """
        调用Ark API进行推理
        使用streaming模式获取reasoning_content和content
        """
        think_token = "think_never_used_51bce0c785ca2f68081bfa7d91973934"
        
        # 尝试不使用 reasoning_effort 参数
        # reasoning_effort='high' 可能导致模型输出简化格式
        completion = self.ark_client.chat.completions.create(
            model=self.model_name,
            stream=True,
            # 流式模式下请求返回 token usage（在最后一个 chunk 中携带）
            stream_options={"include_usage": True},
            reasoning_effort='high',
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p
        )
        
        prediction = ''
        reasoning_content = ''
        content = ''
        added_think_token = False
        
        # 处理流式响应
        for chunk in completion:
            if hasattr(chunk, 'choices') and chunk.choices:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    reasoning_content += delta.reasoning_content
                if hasattr(delta, 'content') and delta.content:
                    if not added_think_token:
                        prediction += f"</{think_token}>"
                        added_think_token = True
                    content += delta.content
            # 提取 token usage（通常在最后一个 chunk 中）
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                self.last_token_usage = {
                    "prompt_tokens": int(getattr(chunk.usage, 'prompt_tokens', 0) or 0),
                    "completion_tokens": int(getattr(chunk.usage, 'completion_tokens', 0) or 0),
                    "total_tokens": int(getattr(chunk.usage, 'total_tokens', 0) or 0),
                }
        
        # 组合完整prediction
        prediction = f"<{think_token}>" + reasoning_content + f"</{think_token}>" + content
        
        # 记录原始 content 到 info 级别日志，用于排查字符丢失问题
        # （之前仅 debug 级别，生产环境中无法回溯模型原始输出）
        self.logger.info(f"[_call_ark_api] RAW reasoning_content length: {len(reasoning_content)}")
        self.logger.info(f"[_call_ark_api] RAW content length: {len(content)}")
        self.logger.info(f"[_call_ark_api] RAW content:\n{'='*60}\n{content}\n{'='*60}")

        # 保存原始 content 到实例变量，供 tool wrapper 写入 JSON 执行记录
        self.last_raw_content = content

        return prediction
    
    def predict(self, task_instruction: str, obs: dict) -> Tuple[Union[str, Dict, None], List]:
        """
        预测下一步动作
        
        Args:
            task_instruction: 任务指令
            obs: 观察结果，包含screenshot等
            
        Returns:
            (prediction_text, actions_list) 或 (prediction_text, ["DONE"/"WAIT"/"FAIL"])
        """
        think_start_time = time.time()
        self.last_round_timing = {
            "think_start": think_start_time,
            "think_end": None,
            "action_start": None,
            "action_end": None,
            "action_result": None
        }
        # 添加sudo密码提示
        task_instruction = task_instruction + f"\nThe sudo password is osworld-public-evaluation"
        
        # 准备screenshot
        screenshot_b64, width, height = self._prepare_screenshot(obs["screenshot"])
        self.history_images.append(screenshot_b64)
        
        # 保存observation
        self.observations.append({
            "screenshot": screenshot_b64,
            "accessibility_tree": None
        })
        
        # 限制历史图片数量
        if len(self.history_images) > self.history_n:
            self.history_images = self.history_images[-self.history_n:]
        
        # 构建messages - 使用官方两段式 prompt
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "system",
                "content": FUNCTION_DEFINITION
            },
            {
                "role": "user",
                "content": task_instruction
            }
        ]
        
        image_num = 0
        if len(self.history_responses) > 0:
            for history_idx, history_response in enumerate(self.history_responses):
                # 只发送最近history_n个图片
                if history_idx + self.history_n > len(self.history_responses):
                    messages.append({
                        "role": "tool",
                        "content": [{
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{self.history_images[image_num]}",
                                "detail": "high"
                            }
                        }],
                        "tool_call_id": "1"
                    })
                    image_num += 1
                
                # 分离reasoning和content
                parts = history_response.split("</think_never_used_51bce0c785ca2f68081bfa7d91973934>")
                content = parts[-1] if len(parts) > 1 else ""
                reasoning = parts[0].replace("<think_never_used_51bce0c785ca2f68081bfa7d91973934>", "") if len(parts) > 1 else ""
                
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning
                })
            
            # 添加当前图片
            messages.append({
                "role": "tool",
                "content": [{
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self.history_images[image_num]}",
                        "detail": "high"
                    }
                }],
                "tool_call_id": "1"
            })
        else:
            # 首次调用，只有当前图片
            messages.append({
                "role": "tool",
                "content": [{
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self.history_images[image_num]}",
                        "detail": "high"
                    }
                }],
                "tool_call_id": "1"
            })
        
        # 调用API
        try_times = 3
        prediction = None
        
        while try_times > 0:
            try:
                self.logger.info(f"[DoubaoSeedGUIAgent] Calling Ark API...")
                prediction = self._call_ark_api(messages)
                # 显示完整的prediction用于调试
                self.logger.info(f"[DoubaoSeedGUIAgent] Got FULL prediction:\n{'='*60}\n{prediction}\n{'='*60}")
                break
            except Exception as e:
                self.logger.error(f"[DoubaoSeedGUIAgent] API call failed: {e}")
                prediction = None
                try_times -= 1
        
        if prediction is None:
            raise ValueError("Failed to get prediction from Ark API")
        
        think_end_time = time.time()
        self.last_round_timing["think_end"] = think_end_time
        
        # 保存响应历史
        self.history_responses.append(prediction)
        
        # 提取content部分（去掉thinking标签）
        parts = prediction.split("</think_never_used_51bce0c785ca2f68081bfa7d91973934>")
        content_part = parts[-1] if len(parts) > 1 else prediction
        
        # 尝试补全简化的XML格式
        expanded_content = self._expand_simplified_xml(content_part)
        
        # 如果补全后内容有变化，重新组合prediction
        if expanded_content != content_part:
            self.logger.info(f"[DoubaoSeedGUIAgent] ✅ XML格式已补全")
            thinking_part = parts[0] + "</think_never_used_51bce0c785ca2f68081bfa7d91973934>" if len(parts) > 1 else ""
            prediction = thinking_part + expanded_content
        
        # 解析 XML actions（官方方式）
        try:
            parsed_responses = parse_xml_action_v3(prediction, GUI_TOOL_SCHEMAS)
            
            # 如果没有解析到任何动作
            if len(parsed_responses) == 0:
                self.logger.warning(f"[DoubaoSeedGUIAgent] ❌ No XML action found in prediction!")
                self.logger.warning(f"[DoubaoSeedGUIAgent] Prediction does NOT contain valid XML function calls")
                self.logger.warning(f"[DoubaoSeedGUIAgent] This means the model only returned thinking without actions")
                self.logger.warning(f"[DoubaoSeedGUIAgent] Possible reasons:")
                self.logger.warning(f"  1. Model doesn't understand the XML function call format")
                self.logger.warning(f"  2. System prompt is not properly configured")
                self.logger.warning(f"  3. Model output was truncated or malformed")
                return prediction, ["DONE"]
                
        except Exception as e:
            self.logger.error(f"[DoubaoSeedGUIAgent] XML Parse error: {e}")
            raise ValueError(f"Parsing XML action error: {e}")
        
        # 提取思考过程
        thoughts = prediction.split("</think_never_used_51bce0c785ca2f68081bfa7d91973934>")[0]
        self.thoughts.append(thoughts)
        
        # 处理每个action
        actions = []
        for parsed_xml_action in parsed_responses:
            parsed_response = {
                "action_type": parsed_xml_action["function"],
                "action_inputs": parsed_xml_action["parameters"]
            }
            
            # 检查终止类action
            if parsed_response["action_type"] == FINISH_WORD:
                self.last_round_timing["action_start"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_end"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_result"] = {"executed": 0, "failed": 0, "errors": []}
                self.actions.append(actions)
                return prediction, ["DONE"]
            
            elif parsed_response["action_type"] == WAIT_WORD:
                self.last_round_timing["action_start"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_end"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_result"] = {"executed": 0, "failed": 0, "errors": []}
                self.actions.append(actions)
                return prediction, ["WAIT"]
            
            elif parsed_response["action_type"] in [ENV_FAIL_WORD, CALL_USER, INFEASIBLE]:
                self.last_round_timing["action_start"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_end"] = self.last_round_timing.get("think_end", think_end_time)
                self.last_round_timing["action_result"] = {"executed": 0, "failed": 0, "errors": []}
                self.actions.append(actions)
                return prediction, ["FAIL"]
            
            # 转换参数格式：将Doubao的参数名转换为parsing_response_to_pyautogui_code期望的格式
            # Doubao使用 'point', 'start_point', 'end_point'，值为0-1000范围的整数
            # parsing_response_to_pyautogui_code期望 'start_box', 'end_box'，值为0-1范围的浮点数
            action_inputs = parsed_response["action_inputs"].copy()

            # 清洗参数值中残留的 XML 闭合标签（如 </audio>, </type> 等）
            # Seed 1.8 模型生成动作参数时可能在值尾部粘连 XML 残片
            COORD_KEYS = {'point', 'start_point', 'end_point', 'start_box', 'end_box'}
            for k, v in action_inputs.items():
                if k not in COORD_KEYS and isinstance(v, str):
                    cleaned = re.sub(r'</\w+>', '', v).strip()
                    if cleaned != v:
                        self.logger.info(f"[DoubaoSeedGUIAgent] XML残片清洗: '{v}' -> '{cleaned}'")
                    action_inputs[k] = cleaned

            # 清洗 type action 的 content 参数中可能混入的 thinking text
            # Seed 1.8 在长上下文下可能在 XML parameter 内生成 thinking 文本
            if parsed_response["action_type"] == "type" and "content" in action_inputs:
                raw_content = action_inputs["content"]
                thinking_pattern = re.compile(
                    r'\n\n(?:Wait|First|Let me|Now|I need|I should|Next|OK|The |So |Then )',
                    re.IGNORECASE
                )
                match = thinking_pattern.search(raw_content)
                if match:
                    cleaned = raw_content[:match.start()]
                    self.logger.info(
                        f"[DoubaoSeedGUIAgent] Thinking text 清洗: "
                        f"'{raw_content[:80]}' -> '{cleaned}'"
                    )
                    action_inputs["content"] = cleaned

            # 转换参数名和坐标值（从0-1000整数归一化到0-1浮点数）
            if "point" in action_inputs:
                # 提取 <point>x y</point> 中的坐标
                point_str = action_inputs["point"]
                match = re.search(r'<point>(\d+)\s+(\d+)</point>', point_str)
                if match:
                    x, y = match.groups()
                    # 归一化：从0-1000范围转换到0-1范围
                    x_norm = float(x) / 1000.0
                    y_norm = float(y) / 1000.0
                    action_inputs["start_box"] = f"({x_norm}, {y_norm})"
                    del action_inputs["point"]
            
            if "start_point" in action_inputs:
                point_str = action_inputs["start_point"]
                match = re.search(r'<point>(\d+)\s+(\d+)</point>', point_str)
                if match:
                    x, y = match.groups()
                    x_norm = float(x) / 1000.0
                    y_norm = float(y) / 1000.0
                    action_inputs["start_box"] = f"({x_norm}, {y_norm})"
                    del action_inputs["start_point"]
            
            if "end_point" in action_inputs:
                point_str = action_inputs["end_point"]
                match = re.search(r'<point>(\d+)\s+(\d+)</point>', point_str)
                if match:
                    x, y = match.groups()
                    x_norm = float(x) / 1000.0
                    y_norm = float(y) / 1000.0
                    action_inputs["end_box"] = f"({x_norm}, {y_norm})"
                    del action_inputs["end_point"]
            
            # 更新action_inputs
            parsed_response["action_inputs"] = action_inputs
            
            # 转换为pyautogui代码
            try:
                pyautogui_code = parsing_response_to_pyautogui_code(
                    parsed_response,
                    height,
                    width,
                    input_swap=False
                )
                actions.append(pyautogui_code)
            except Exception as e:
                self.logger.error(f"[DoubaoSeedGUIAgent] Failed to convert action to pyautogui: {e}")
                self.logger.error(f"[DoubaoSeedGUIAgent] Action was: {parsed_response}")
                # 如果转换失败，跳过这个action
                continue
        
        self.actions.append(actions)
        
        # 在 agent 内部执行动作（与 Claude 执行路径对齐）
        if self.execute_actions and isinstance(actions, list):
            action_start_time = time.time()
            action_result = self._execute_actions(actions)
            action_end_time = time.time()
            self.last_round_timing["action_start"] = action_start_time
            self.last_round_timing["action_end"] = action_end_time
            self.last_round_timing["action_result"] = action_result
        else:
            self.last_round_timing["action_start"] = think_end_time
            self.last_round_timing["action_end"] = think_end_time
            self.last_round_timing["action_result"] = {"executed": 0, "failed": 0, "errors": []}
        
        return prediction, actions
