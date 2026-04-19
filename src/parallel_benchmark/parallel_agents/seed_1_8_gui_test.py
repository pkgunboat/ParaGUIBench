#!/usr/bin/env python3
"""
Seed 1.8 GUI Agent 交互测试脚本

测试循环：连接VM → 获取截图 → 发送给 Seed 1.8 模型 → 解析动作 → 在 VM 上执行 → 重复

使用方法：
    conda activate parallelbenchmark
    cd ubuntu_env
    python parallel_benchmark/parallel_agents/seed_1_8_gui_test.py \
        --task "打开Firefox浏览器并访问百度" \
        --vm-ip 10.1.110.114 \
        --server-port 5000 \
        --max-steps 20

依赖：
    - openai SDK（通过 DeerAPI 代理调用，或直连火山引擎时可选 Ark SDK）
    - Pillow (图像处理)
    - requests (VM 通信)
"""

import os
import sys
import re
import base64
import time
import json
import io
import argparse
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from PIL import Image

# SDK 延迟导入：在 __init__ 中根据用户选择导入对应的 SDK

# ============================================================
# 路径设置：确保能导入项目内的模块
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UBUNTU_ENV_DIR = os.path.join(SCRIPT_DIR, '..', '..')
sys.path.insert(0, os.path.abspath(UBUNTU_ENV_DIR))
sys.path.insert(0, os.path.abspath(os.path.join(SCRIPT_DIR, '..')))

from desktop_env.controllers.python import PythonController
from utils.xml_action_parser import parse_xml_action_v3


# ============================================================
# 常量定义
# ============================================================

# Seed 模型 XML 解析中使用的特殊 token
THINK_TOKEN = "think_never_used_51bce0c785ca2f68081bfa7d91973934"
FUNC_TOKEN = "function_never_used_51bce0c785ca2f68081bfa7d91973934"
PARAM_TOKEN = "parameter_never_used_51bce0c785ca2f68081bfa7d91973934"

# 终止动作类型
TERMINAL_ACTIONS = {"finished", "call_user", "infeasible", "error_env"}

# GUI 工具 Schema（Seed 模型需要的工具定义列表）
GUI_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "click", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left single click action."}},
    {"type": "function", "function": {"name": "left_double", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left double click action."}},
    {"type": "function", "function": {"name": "right_single", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse right single click action."}},
    {"type": "function", "function": {"name": "drag", "parameters": {"type": "object", "properties": {"start_point": {"type": "string", "description": "Drag start point. The format is: <point>x y</point>"}, "end_point": {"type": "string", "description": "Drag end point. The format is: <point>x y</point>"}}, "required": ["start_point", "end_point"]}, "description": "Mouse left button drag action."}},
    {"type": "function", "function": {"name": "scroll", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Scroll start position. The format is: <point>x y</point>"}, "direction": {"type": "string", "description": "Scroll direction.", "enum": ["up", "down", "left", "right"]}}, "required": ["direction"]}, "description": "Scroll action."}},
    {"type": "function", "function": {"name": "type", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Type content. If you want to submit your input, use \\n at the end of content."}}, "required": ["content"]}, "description": "Type content."}},
    {"type": "function", "function": {"name": "hotkey", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Hotkeys you want to press. Split keys with a space and use lowercase."}}, "required": ["key"]}, "description": "Press hotkey."}},
    {"type": "function", "function": {"name": "press", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Key you want to press. Only one key can be pressed at one time."}}, "required": ["key"]}, "description": "Press key."}},
    {"type": "function", "function": {"name": "wait", "parameters": {"type": "object", "properties": {"time": {"type": "integer", "description": "Wait time in seconds."}}, "required": []}, "description": "Wait for a while."}},
    {"type": "function", "function": {"name": "finished", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Provide the final answer or response to complete the task."}}, "required": []}, "description": "This function is used to indicate the completion of a task by providing the final answer or response."}},
    {"type": "function", "function": {"name": "call_user", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user."}}, "required": []}, "description": "Interact with the user by displaying a message."}},
    {"type": "function", "function": {"name": "infeasible", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Explain why the task is infeasible."}}, "required": ["content"]}, "description": "Indicate the task is infeasible."}},
]

# Seed 模型系统提示词（包含工具定义和调用格式说明）
SYSTEM_PROMPT_ROLE = "You are provided with a task description, a history of previous actions, and corresponding screenshots. Your goal is to perform the next action to complete the task. Please note that if performing the same action multiple times results in a static screen with no changes, you should attempt a modified or alternative action."

SYSTEM_PROMPT_TOOLS = '''## Function Definition

- You have access to the following functions:
{"type": "function", "name": "call_user", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to request their input, feedback, or guidance."}}, "required": []}, "description": "This function is used to interact with the user by displaying a message and requesting their input, feedback, or guidance."}
{"type": "function", "name": "click", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left single click action."}
{"type": "function", "name": "drag", "parameters": {"type": "object", "properties": {"start_point": {"type": "string", "description": "Drag start point. The format is: <point>x y</point>"}, "end_point": {"type": "string", "description": "Drag end point. The format is: <point>x y</point>"}}, "required": ["start_point", "end_point"]}, "description": "Mouse left button drag action."}
{"type": "function", "name": "finished", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Provide the final answer or response to complete the task."}}, "required": []}, "description": "This function is used to indicate the completion of a task by providing the final answer or response."}
{"type": "function", "name": "hotkey", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Hotkeys you want to press. Split keys with a space and use lowercase."}}, "required": ["key"]}, "description": "Press hotkey."}
{"type": "function", "name": "infeasible", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Message or information displayed to the user to explain why the current task is infeasible."}}, "required": ["content"]}, "description": "This function is used to indicate that the current task is infeasible thus agent ends the task."}
{"type": "function", "name": "left_double", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse left double click action."}
{"type": "function", "name": "right_single", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Click coordinates. The format is: <point>x y</point>"}}, "required": ["point"]}, "description": "Mouse right single click action."}
{"type": "function", "name": "scroll", "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Scroll start position. If not specified, default to execute on the current mouse position. The format is: <point>x y</point>"}, "direction": {"type": "string", "description": "Scroll direction.", "enum": ["up", "down", "left", "right"]}}, "required": ["direction", "point"]}, "description": "Scroll action."}
{"type": "function", "name": "type", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Type content. If you want to submit your input, use \\n at the end of content."}}, "required": ["content"]}, "description": "Type content."}
{"type": "function", "name": "wait", "parameters": {"type": "object", "properties": {"time": {"type": "integer", "description": "Wait time in seconds."}}, "required": []}, "description": "Wait for a while."}

- To call a function, use the following structure without any suffix:

<THINK_TOKEN> reasoning process </THINK_TOKEN>
<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><FUNC_TOKEN=example_function_name><PARAM_TOKEN=example_parameter_1>value_1</PARAM_TOKEN><PARAM_TOKEN=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</PARAM_TOKEN></FUNC_TOKEN></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>

## Important Notes
- Function calls must begin with <FUNC_TOKEN= and end with </FUNC_TOKEN>.
- All required parameters must be explicitly provided.

## Additional Notes
- You can execute multiple actions within a single tool call.'''.replace("THINK_TOKEN", THINK_TOKEN).replace("FUNC_TOKEN", FUNC_TOKEN).replace("PARAM_TOKEN", PARAM_TOKEN)


# ============================================================
# 从 GUI_TOOL_SCHEMAS 中提取函数名 → 参数名列表的映射表
# ============================================================
KNOWN_FUNCTIONS: Dict[str, List[str]] = {}
for _schema in GUI_TOOL_SCHEMAS:
    _func = _schema.get("function", _schema)
    _name = _func.get("name", "")
    _params = list(_func.get("parameters", {}).get("properties", {}).keys())
    KNOWN_FUNCTIONS[_name] = _params


# ============================================================
# 工具函数
# ============================================================

def parse_seed_fragment(content: str) -> List[Dict[str, Any]]:
    """
    解析 Seed 模型输出经 API 处理后的残片格式。
    
    当 API 将 function_never_used_51bce0c785ca2f68081bfa7d91973934 等特殊 token 
    从文本中剥离后，content 中残留的格式为：
        click>point><point>18 59</point>
        type>content>hello world
        hotkey>key>ctrl a
        scroll>point><point>500 500</point>direction>down
        drag>start_point><point>100 200</point>end_point><point>300 400</point>
        finished>content>task done
    
    本函数利用已知的函数名和参数名从这种残片中提取动作。
    
    注意：参数值中可能包含 <point>x y</point>，其中 point> 也会被正则匹配到。
    因此使用 negative lookbehind (?<![</]) 排除 XML 标签内部的 point> 匹配。
    
    输入:
        content: 模型输出的 content 文本（特殊 token 已被剥离）
    输出:
        解析后的 actions 列表，与 parse_xml_action_v3 格式一致：
        [{'function': 'click', 'parameters': {'point': '<point>18 59</point>'}}]
    """
    content = content.strip()
    if not content:
        return []
    
    # 构建函数名匹配正则（按名称长度降序，避免短名称误匹配）
    sorted_func_names = sorted(KNOWN_FUNCTIONS.keys(), key=len, reverse=True)
    func_names_pattern = '|'.join(re.escape(n) for n in sorted_func_names)
    
    # 用前瞻正则将文本按「函数名>」分段（排除 <func_name> 和 </func_name> 中的误匹配）
    split_pattern = rf'(?=(?<![</])(?:{func_names_pattern})>)'
    segments = re.split(split_pattern, content)
    
    actions = []
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        
        # 提取函数名：段首 "func_name>" 
        func_match = re.match(rf'({func_names_pattern})>(.*)', segment, re.DOTALL)
        if not func_match:
            continue
        
        func_name = func_match.group(1)
        rest = func_match.group(2).strip()
        
        # 根据已知参数名解析参数值
        expected_params = KNOWN_FUNCTIONS.get(func_name, [])
        parameters: Dict[str, str] = {}
        
        if not expected_params or not rest:
            actions.append({'function': func_name, 'parameters': parameters})
            continue
        
        # 按参数名长度降序排列（避免 "point" 先于 "start_point" 匹配）
        sorted_params = sorted(expected_params, key=len, reverse=True)
        param_names_pattern = '|'.join(re.escape(p) for p in sorted_params)
        
        # 使用 finditer 定位参数分界点
        # (?<![</]) 排除 <point> 和 </point> 中的 point> 误匹配
        param_boundary_pattern = rf'(?<![</])({param_names_pattern})>'
        matches = list(re.finditer(param_boundary_pattern, rest))
        
        for i, m in enumerate(matches):
            p_name = m.group(1)
            value_start = m.end()
            # 值延伸到下一个参数分界点之前，或文本末尾
            value_end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
            p_value = rest[value_start:value_end].strip()
            p_value = _truncate_reasoning_tail(p_value)  # 截断尾部混入的推理文本
            parameters[p_name] = p_value
        
        actions.append({'function': func_name, 'parameters': parameters})
    
    return actions


def parse_point(point_str: str) -> Tuple[int, int]:
    """
    从 Seed 模型输出的 point 字符串中提取坐标。
    
    输入格式: "<point>x y</point>" 或 "x y" 或纯数字
    输出: (x, y)，坐标范围 [0, 1000]
    """
    # 匹配 <point>x y</point> 格式
    match = re.search(r'<point>\s*(\d+)\s+(\d+)\s*</point>', point_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    
    # 匹配纯数字 "x y" 格式
    match = re.search(r'(\d+)\s+(\d+)', point_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    
    raise ValueError(f"无法解析 point 字符串: {point_str}")


def point_to_pixel(point_x: int, point_y: int, img_width: int, img_height: int) -> Tuple[int, int]:
    """
    将 Seed 模型坐标（0-1000 范围）转换为实际像素坐标。
    
    输入:
        point_x, point_y: Seed 模型输出的坐标，范围 [0, 1000]
        img_width, img_height: 截图的实际像素尺寸
    输出:
        (pixel_x, pixel_y): 实际像素坐标
    """
    pixel_x = round(point_x * img_width / 1000)
    pixel_y = round(point_y * img_height / 1000)
    return pixel_x, pixel_y


def _clean_xml_residue(value: str) -> str:
    """
    清洗参数值中残留的 XML 闭合标签。

    Seed 1.8 模型在生成动作参数时，可能在参数值尾部粘连 XML 闭合标签残片，
    例如 'Jessica</type>'、'backspace</audio>'、'Main Street 123</audio>' 等。
    本函数移除这些非法残片，但保留合法的 <point>x y</point> 坐标格式。

    输入:
        value: 原始参数值字符串
    输出:
        清洗后的参数值字符串
    """
    if not isinstance(value, str):
        return value
    # 移除所有 </xxx> 格式的闭合标签（但不移除 <point>...</point> 中的开始标签）
    # 常见残片：</audio>, </type>, </parameter>, </function>, </point>（出现在非坐标字段时）
    cleaned = re.sub(r'</\w+>', '', value)
    # 移除清洗后可能出现的尾部空白
    return cleaned.strip()


def _truncate_reasoning_tail(value: str) -> str:
    """
    截断参数值尾部混入的推理文本。

    模型有时在动作参数后紧跟自我纠正文本（如 "Wait, no, ..."、"Let me ..."），
    需要在参数值中将其移除。

    策略：在第一个换行符处检测后续文本是否像推理文本，是则截断。

    输入:
        value: 原始参数值字符串
    输出:
        截断推理文本后的参数值字符串
    """
    # 推理文本起始模式
    REASONING_PATTERNS = [
        r'Wait,?\s',
        r'Let me\s',
        r'Oh\s',
        r'Yes,?\s',
        r'No,?\s',
        r'Actually',
        r'Hmm',
        r'I (need|should|think|want)',
        r'The \w+ function is',
    ]
    pattern = re.compile(r'\n\s*(' + '|'.join(REASONING_PATTERNS) + ')', re.IGNORECASE)
    match = pattern.search(value)
    if match:
        return value[:match.start()].strip()
    return value


def seed_action_to_pyautogui(action: Dict[str, Any], img_width: int, img_height: int) -> str:
    """
    将 Seed 模型解析出的单个动作转换为 pyautogui 可执行代码字符串。

    输入:
        action: parse_xml_action_v3 解析后的单个动作字典
            格式: {'function': 'click', 'parameters': {'point': '<point>500 300</point>'}}
        img_width, img_height: 截图的实际像素尺寸
    输出:
        pyautogui 代码字符串，例如 "import pyautogui\npyautogui.click(960, 540)"
    """
    func_name = _clean_xml_residue(action['function'])
    params = action['parameters']

    # 对所有非坐标参数值进行 XML 残片清洗
    # 坐标字段 (point, start_point, end_point) 包含合法的 <point>x y</point>，
    # 由 parse_point() 用正则提取数字，残片不影响解析，但仍然清洗以保持一致性
    COORD_KEYS = {'point', 'start_point', 'end_point'}
    cleaned_params = {}
    for k, v in params.items():
        if k in COORD_KEYS:
            # 坐标字段：只移除非 <point>...</point> 的残片标签
            # 先提取合法坐标，再清洗其余部分
            cleaned_params[k] = v  # parse_point 本身用正则提取，残片不影响
        else:
            cleaned_params[k] = _clean_xml_residue(v)
    params = cleaned_params
    
    code_lines = ["import pyautogui", "import time", "pyautogui.FAILSAFE = False"]
    
    if func_name == 'click':
        # 鼠标左键单击
        px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
        code_lines.append(f"pyautogui.click({px}, {py}, button='left')")
    
    elif func_name == 'left_double':
        # 鼠标左键双击（使用两次快速单击，VNC 环境更可靠）
        px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
        code_lines.append(f"pyautogui.click({px}, {py}, button='left')")
        code_lines.append("time.sleep(0.1)")
        code_lines.append(f"pyautogui.click({px}, {py}, button='left')")
    
    elif func_name == 'right_single':
        # 鼠标右键单击
        px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
        code_lines.append(f"pyautogui.click({px}, {py}, button='right')")
    
    elif func_name == 'drag':
        # 拖拽操作（需要 start_point 和 end_point 两个参数）
        if 'start_point' not in params or 'end_point' not in params:
            print(f"[WARN] drag 动作缺少必要参数: start_point={'start_point' in params}, end_point={'end_point' in params}")
            return None
        sx, sy = point_to_pixel(*parse_point(params['start_point']), img_width, img_height)
        ex, ey = point_to_pixel(*parse_point(params['end_point']), img_width, img_height)
        code_lines.append(f"pyautogui.moveTo({sx}, {sy})")
        code_lines.append(f"pyautogui.dragTo({ex}, {ey}, duration=1.0)")
    
    elif func_name == 'scroll':
        # 滚动操作
        direction = params.get('direction', 'down')
        scroll_amount = 5  # 滚动量
        if 'point' in params and params['point']:
            px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
            if direction == 'up':
                code_lines.append(f"pyautogui.scroll({scroll_amount}, x={px}, y={py})")
            elif direction == 'down':
                code_lines.append(f"pyautogui.scroll(-{scroll_amount}, x={px}, y={py})")
        else:
            if direction == 'up':
                code_lines.append(f"pyautogui.scroll({scroll_amount})")
            elif direction == 'down':
                code_lines.append(f"pyautogui.scroll(-{scroll_amount})")
    
    elif func_name == 'move_to':
        # 鼠标移动
        px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
        code_lines.append(f"pyautogui.moveTo({px}, {py})")
    
    elif func_name == 'type':
        # 文字输入
        content = params.get('content', '')
        # 判断是否以换行符结尾（表示需要按回车）
        needs_enter = content.endswith('\n') or content.endswith('\\n')
        # 移除尾部的换行符：先处理字面量 '\\n'（两个字符），再处理真实换行符
        # 注意：不能使用 rstrip('\\n')，因为 rstrip 将参数视为字符集合 {'\\', 'n'}，
        # 会错误地剥离尾部的 'n' 字符（如 "Morgan" → "Morga"）
        stripped = content
        while stripped.endswith('\\n'):
            stripped = stripped[:-2]
        stripped = stripped.rstrip('\n')
        if stripped:
            code_lines.append(f"pyautogui.write({repr(stripped)}, interval=0.05)")
            code_lines.append("time.sleep(0.5)")
        if needs_enter:
            code_lines.append("pyautogui.press('enter')")
    
    elif func_name == 'hotkey':
        # 组合键
        key_str = params.get('key', '')
        keys = key_str.split()
        # 校验每个键名合法性，过滤推理文本残留
        VALID_HOTKEYS = {
            'ctrl', 'shift', 'alt', 'super', 'win', 'command', 'tab', 'enter',
            'return', 'space', 'backspace', 'delete', 'escape', 'esc',
            'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown',
            'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
            'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
            'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
            '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
            'plus', 'minus', 'equal',
        }
        valid_keys = []
        for k in keys:
            k_lower = k.lower().rstrip(',.')  # 去除尾部标点
            if k_lower in VALID_HOTKEYS or len(k_lower) == 1:
                valid_keys.append(k_lower)
            else:
                print(f"  [WARN] hotkey 忽略非法键名: {k}（后续视为推理文本残留）")
                break  # 遇到非法键名就停止，后续都是推理文本
        keys = valid_keys if valid_keys else keys[:2]  # fallback: 至少保留前两个
        if len(keys) == 1:
            code_lines.append(f"pyautogui.press({repr(keys[0])})")
        elif len(keys) > 1:
            # 使用 keyDown/keyUp 模式确保组合键可靠
            for k in keys[:-1]:
                code_lines.append(f"pyautogui.keyDown({repr(k)})")
            code_lines.append(f"pyautogui.press({repr(keys[-1])})")
            for k in reversed(keys[:-1]):
                code_lines.append(f"pyautogui.keyUp({repr(k)})")
    
    elif func_name == 'press':
        # 按键
        key = params.get('key', '')
        if key:
            code_lines.append(f"pyautogui.press({repr(key)})")
    
    elif func_name == 'release':
        # 释放按键
        key = params.get('key', '')
        if key:
            code_lines.append(f"pyautogui.keyUp({repr(key)})")
    
    elif func_name == 'mouse_down':
        button = params.get('button', 'left')
        if 'point' in params and params['point']:
            px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
            code_lines.append(f"pyautogui.moveTo({px}, {py})")
        code_lines.append(f"pyautogui.mouseDown(button={repr(button)})")
    
    elif func_name == 'mouse_up':
        button = params.get('button', 'left')
        if 'point' in params and params['point']:
            px, py = point_to_pixel(*parse_point(params['point']), img_width, img_height)
            code_lines.append(f"pyautogui.moveTo({px}, {py})")
        code_lines.append(f"pyautogui.mouseUp(button={repr(button)})")
    
    elif func_name == 'wait':
        # 防御性解析：time 参数可能包含残片尾缀（如 '10</point>'），先提取数字
        raw_time = str(params.get('time', 3))
        time_match = re.search(r'\d+', raw_time)
        wait_time = int(time_match.group()) if time_match else 3
        code_lines.append(f"time.sleep({wait_time})")
    
    elif func_name in TERMINAL_ACTIONS:
        # 终止动作不需要生成代码
        return None
    
    else:
        print(f"  [警告] 未知动作类型: {func_name}，跳过")
        return None
    
    return "; ".join(code_lines)


def extract_thinking_and_content(prediction: str) -> Tuple[str, str]:
    """
    从模型完整输出中分离 thinking 和 content 部分。
    
    输入:
        prediction: 模型的完整输出文本（包含 <think>...</think> 标签）
    输出:
        (thinking, content): 分别为思考过程和实际内容
    """
    think_close_tag = f"</{THINK_TOKEN}>"
    if think_close_tag in prediction:
        parts = prediction.split(think_close_tag, 1)
        thinking = parts[0].replace(f"<{THINK_TOKEN}>", "").strip()
        content = parts[1].strip()
    else:
        thinking = ""
        content = prediction.strip()
    return thinking, content


# ============================================================
# 核心类：Seed 1.8 GUI 测试器
# ============================================================

class Seed18GUITester:
    """
    Seed 1.8 GUI Agent 测试器。
    
    实现完整的测试循环：
        连接VM → 获取截图 → 发送给模型 → 解析动作 → 执行动作 → 重复
    
    参数:
        vm_ip: 虚拟机 IP 地址
        server_port: VM Python Server 端口
        model: Seed 模型名称
        api_key: API Key
        base_url: API Base URL
        max_steps: 最大执行步数
        max_tokens: 模型最大生成 token 数
        temperature: 采样温度
        top_p: Top-P 采样参数
        history_n: 保留的历史截图数量
        action_pause: 每步动作后的等待时间（秒）
        save_screenshots: 是否保存截图到磁盘
        output_dir: 截图和日志保存目录
    """
    
    def __init__(
        self,
        vm_ip: str = "10.1.110.114",
        server_port: int = 5000,
        model: str = "doubao-seed-1-8-251228",
        api_key: str = "",
        base_url: str = "https://api.deerapi.com/v1/",
        sdk: str = "openai",
        max_steps: int = 50,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        top_p: float = 0.9,
        history_n: int = 5,
        action_pause: float = 2.0,
        save_screenshots: bool = True,
        output_dir: Optional[str] = None,
    ):
        """
        初始化测试器。

        参数:
            sdk: 使用的 SDK 类型，可选 "openai"（默认，DeerAPI 兼容）或 "ark"（火山引擎原生）。
                 - openai: 使用 openai.OpenAI，通过 DeerAPI 代理调用 Seed 模型，
                           API 会剥离 Seed 模型的特殊 XML token（由残片解析器兜底）。
                 - ark:    使用 volcenginesdkarkruntime.Ark，支持 reasoning_effort 参数，
                           适用于直连火山引擎场景。
                 两种 SDK 最终效果一致，均依赖 parse_seed_fragment 残片解析器提取动作。
        """
        # 模型配置
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.sdk = sdk.lower()
        
        # VM 配置
        self.vm_ip = vm_ip
        self.server_port = server_port
        
        # 运行配置
        self.max_steps = max_steps
        self.history_n = history_n
        self.action_pause = action_pause
        self.save_screenshots = save_screenshots
        
        # 输出目录
        if output_dir is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = os.path.join(SCRIPT_DIR, '..', 'logs', f'seed18_test_{timestamp}')
        self.output_dir = os.path.abspath(output_dir)
        
        # 根据 sdk 参数初始化对应的客户端
        if self.sdk == "openai":
            from openai import OpenAI
            self.client = OpenAI(
                base_url=base_url,
                api_key=api_key,
            )
        else:
            # 默认使用 Ark SDK
            from volcenginesdkarkruntime import Ark
            self.client = Ark(
                base_url=base_url,
                api_key=api_key,
            )
        
        # 初始化 VM 控制器
        self.controller = PythonController(vm_ip=vm_ip, server_port=server_port)
        
        # 历史记录
        self.history_images: List[str] = []       # base64 截图列表
        self.history_responses: List[str] = []    # 模型原始输出列表
    
    def _ensure_output_dir(self):
        """确保输出目录存在。"""
        if self.save_screenshots:
            os.makedirs(self.output_dir, exist_ok=True)
    
    def _get_screenshot(self) -> Tuple[Optional[str], int, int]:
        """
        从 VM 获取截图并转为 base64。
        
        输出:
            (base64_str, width, height): base64 编码的截图、宽度、高度
            如果获取失败返回 (None, 0, 0)
        """
        screenshot_bytes = self.controller.get_screenshot()
        if screenshot_bytes is None:
            return None, 0, 0
        
        image = Image.open(io.BytesIO(screenshot_bytes))
        width, height = image.size
        b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        return b64, width, height
    
    def _call_model(self, messages: List[Dict]) -> Dict[str, Any]:
        """
        流式调用 Seed 模型，支持 Ark SDK 和 OpenAI SDK 两种后端。
        
        两种 SDK 的流式响应结构一致（都有 delta.reasoning_content 和 delta.content），
        主要区别在于 Ark SDK 额外支持 reasoning_effort 参数。
        
        输入:
            messages: 消息列表
        输出:
            字典，包含以下字段：
            - reasoning_content: str, 模型的思考过程
            - content: str, 模型的文本输出
            - tool_calls: list, API 返回的 tool_calls（通常为空）
            - raw_prediction: str, 组装后的完整输出文本（用于 XML 解析和历史记录）
        """
        # 构建请求参数（两种 SDK 共用的基础参数）
        from parallel_benchmark.utils.llm_determinism import (
            LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
        )
        create_kwargs = dict(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
            top_p=self.top_p,
            stream=True,
            # 流式模式下请求返回 token usage（在最后一个 chunk 中携带）
            stream_options={"include_usage": True},
        )
        # Ark SDK 支持 reasoning_effort 参数
        if self.sdk == "ark":
            create_kwargs["reasoning_effort"] = "high"

        assert_deterministic(create_kwargs)
        completion = self.client.chat.completions.create(**create_kwargs)
        
        reasoning_content = ''
        content = ''
        # tool_calls 收集（OpenAI 兼容层可能返回）
        tool_calls_dict: Dict[int, Dict] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        
        for chunk in completion:
            # 部分后端会在流式块中携带 usage（通常在最后一个 chunk）
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                usage["prompt_tokens"] = int(getattr(chunk.usage, 'prompt_tokens', 0) or 0)
                usage["completion_tokens"] = int(getattr(chunk.usage, 'completion_tokens', 0) or 0)
                usage["total_tokens"] = int(getattr(chunk.usage, 'total_tokens', 0) or 0)
            if not hasattr(chunk, 'choices') or not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            
            # 收集 reasoning_content（thinking 部分）
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                reasoning_content += delta.reasoning_content
            
            # 收集 content
            if hasattr(delta, 'content') and delta.content:
                content += delta.content
            
            # 收集 tool_calls（OpenAI 兼容层可能将 Seed XML 转换为此格式）
            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if hasattr(tc, 'index') else 0
                    if idx not in tool_calls_dict:
                        tool_calls_dict[idx] = {'function_name': '', 'arguments': ''}
                    if hasattr(tc, 'function') and tc.function:
                        if hasattr(tc.function, 'name') and tc.function.name:
                            tool_calls_dict[idx]['function_name'] = tc.function.name
                        if hasattr(tc.function, 'arguments') and tc.function.arguments:
                            tool_calls_dict[idx]['arguments'] += tc.function.arguments
        
        # 组装 tool_calls 列表
        tool_calls = []
        for idx in sorted(tool_calls_dict.keys()):
            tc = tool_calls_dict[idx]
            tool_calls.append({
                'function': tc['function_name'],
                'arguments_raw': tc['arguments'],
            })
        
        # 组装 raw_prediction：thinking + content，用于 XML 解析
        raw_prediction = f"<{THINK_TOKEN}>{reasoning_content}</{THINK_TOKEN}>{content}"
        
        return {
            'reasoning_content': reasoning_content,
            'content': content,
            'tool_calls': tool_calls,
            'raw_prediction': raw_prediction,
            'usage': usage,
        }
    
    def _build_messages(self, task_instruction: str) -> List[Dict]:
        """
        构建发送给模型的消息列表，包含系统提示、任务指令和历史截图/响应。
        
        输入:
            task_instruction: 用户任务指令
        输出:
            OpenAI 格式的消息列表
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_ROLE},
            {"role": "system", "content": SYSTEM_PROMPT_TOOLS},
            {"role": "user", "content": task_instruction + "\nThe sudo password is osworld-public-evaluation"},
        ]
        
        # 计算历史窗口起始位置（只保留最近 history_n 轮的截图）
        total_rounds = len(self.history_responses)
        history_img_start = max(0, total_rounds - self.history_n + 1)  # +1 因为当前截图也要发
        
        if total_rounds > 0:
            # 添加历史轮次
            for idx, response_text in enumerate(self.history_responses):
                # 只在历史窗口内发送截图
                if idx >= history_img_start:
                    messages.append({
                        "role": "tool",
                        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.history_images[idx]}"}}],
                        "tool_call_id": "1"
                    })
                
                # 添加历史响应（分离 thinking 和 content）
                thinking, content_text = extract_thinking_and_content(response_text)
                messages.append({
                    "role": "assistant",
                    "content": content_text,
                    "reasoning_content": thinking,
                })
            
            # 添加当前截图
            messages.append({
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.history_images[-1]}"}}],
                "tool_call_id": "1"
            })
        else:
            # 第一轮：只发送当前截图
            messages.append({
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.history_images[-1]}"}}],
                "tool_call_id": "1"
            })
        
        return messages
    
    def _parse_tool_calls_to_actions(self, tool_calls: List[Dict]) -> List[Dict[str, Any]]:
        """
        将 API 返回的 OpenAI 格式 tool_calls 转换为统一的 parsed_actions 格式。
        
        Ark API 可能将 Seed 模型的特殊 XML token 自动转换为 OpenAI tool_calls 格式，
        此函数负责将其还原为与 parse_xml_action_v3 相同的输出格式。
        
        输入:
            tool_calls: _call_model 返回的 tool_calls 列表
                格式: [{'function': 'click', 'arguments_raw': '{"point":"<point>18 59</point>"}'}]
        输出:
            统一格式的 actions 列表
                格式: [{'function': 'click', 'parameters': {'point': '<point>18 59</point>'}}]
        """
        actions = []
        for tc in tool_calls:
            func_name = tc.get('function', '')
            args_raw = tc.get('arguments_raw', '{}')
            
            # 尝试 JSON 解析参数
            try:
                params = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                # 如果不是合法 JSON，尝试作为纯文本处理
                print(f"        [警告] tool_call 参数非 JSON: {args_raw[:100]}")
                params = {"raw": args_raw}
            
            if func_name:
                actions.append({
                    'function': func_name,
                    'parameters': params,
                })
        return actions
    
    def _save_screenshot(self, step: int, screenshot_b64: str):
        """保存截图到磁盘。"""
        if not self.save_screenshots:
            return
        filepath = os.path.join(self.output_dir, f"step_{step:02d}_screenshot.png")
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(screenshot_b64))
    
    def _save_log(self, log_data: Dict):
        """保存执行日志到磁盘。"""
        if not self.save_screenshots:
            return
        filepath = os.path.join(self.output_dir, "execution_log.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
    
    def run(self, task_instruction: str):
        """
        运行完整的 GUI Agent 测试循环。
        
        输入:
            task_instruction: 要执行的任务指令
        """
        self._ensure_output_dir()
        
        print("=" * 70)
        print("  Seed 1.8 GUI Agent 交互测试")
        print("=" * 70)
        print(f"  模型: {self.model}")
        print(f"  SDK:  {self.sdk}")
        print(f"  VM:   {self.vm_ip}:{self.server_port}")
        print(f"  任务: {task_instruction}")
        print(f"  最大步数: {self.max_steps}")
        print(f"  输出目录: {self.output_dir}")
        print("=" * 70)
        
        # 重置历史
        self.history_images = []
        self.history_responses = []
        
        # 执行日志
        execution_log = {
            "task": task_instruction,
            "model": self.model,
            "vm": f"{self.vm_ip}:{self.server_port}",
            "start_time": datetime.now().isoformat(),
            "steps": [],
        }
        
        # 测试 VM 连通性
        print("\n[初始化] 测试 VM 连接...")
        test_screenshot, _, _ = self._get_screenshot()
        if test_screenshot is None:
            print("[错误] 无法连接到 VM，请检查 IP 和端口是否正确。")
            return
        print("[初始化] VM 连接成功！")
        
        # ========== 主循环 ==========
        for step in range(self.max_steps):
            step_start_time = time.time()
            print(f"\n{'─' * 60}")
            print(f"  Step {step + 1}/{self.max_steps}")
            print(f"{'─' * 60}")
            
            step_log = {"step": step + 1}
            
            # ---- 1. 获取截图 ----
            print(f"  [1/4] 获取截图...")
            screenshot_b64, img_width, img_height = self._get_screenshot()
            if screenshot_b64 is None:
                print(f"  [错误] 获取截图失败，跳过本步")
                step_log["error"] = "screenshot_failed"
                execution_log["steps"].append(step_log)
                continue
            
            print(f"        截图尺寸: {img_width}x{img_height}")
            self.history_images.append(screenshot_b64)
            self._save_screenshot(step, screenshot_b64)
            
            # ---- 2. 调用模型 ----
            print(f"  [2/4] 调用 Seed 1.8 模型...")
            messages = self._build_messages(task_instruction)
            
            try:
                inference_start = time.time()
                model_result = self._call_model(messages)
                inference_time = time.time() - inference_start
                print(f"        推理耗时: {inference_time:.1f}s")
            except Exception as e:
                print(f"  [错误] 模型调用失败: {e}")
                step_log["error"] = f"model_error: {e}"
                execution_log["steps"].append(step_log)
                # 移除本轮截图（因为没有对应的响应）
                self.history_images.pop()
                continue
            
            # 提取各部分内容
            thinking = model_result['reasoning_content']
            content_text = model_result['content']
            tool_calls = model_result['tool_calls']
            raw_prediction = model_result['raw_prediction']
            
            self.history_responses.append(raw_prediction)
            
            # 打印 thinking
            if thinking:
                display_thinking = thinking[:500] + "..." if len(thinking) > 500 else thinking
                print(f"        [Thinking] {display_thinking}")
            
            # 打印 content（完整输出，不截断，方便调试）
            if content_text:
                print(f"        [Content]  {content_text}")
            
            # 打印 tool_calls（如果有）
            if tool_calls:
                print(f"        [ToolCalls] 收到 {len(tool_calls)} 个 tool_call:")
                for i, tc in enumerate(tool_calls):
                    print(f"          #{i+1}: {tc['function']}({tc['arguments_raw'][:200]})")
            
            step_log["thinking"] = thinking
            step_log["content"] = content_text
            step_log["tool_calls_raw"] = tool_calls
            step_log["inference_time"] = round(inference_time, 2)
            
            # ---- 3. 解析动作 ----
            print(f"  [3/4] 解析动作...")
            parsed_actions = []
            action_source = ""
            
            # 方式 1：从完整 XML 解析（如果 API 保留了特殊 token）
            try:
                parsed_actions = parse_xml_action_v3(raw_prediction, GUI_TOOL_SCHEMAS)
                if parsed_actions:
                    action_source = "xml_parse"
                    print(f"        来源: XML 完整解析")
            except Exception as e:
                print(f"        [信息] XML 解析未命中: {e}")
            
            # 方式 2：从残片格式解析（API 剥离了特殊 token 后的格式）
            # 例如 "click>point><point>18 59</point>"
            if not parsed_actions and content_text:
                parsed_actions = parse_seed_fragment(content_text)
                if parsed_actions:
                    action_source = "fragment_parse"
                    print(f"        来源: 残片格式解析（特殊 token 已被 API 剥离）")
            
            # 方式 3：从 API tool_calls 中提取
            if not parsed_actions and tool_calls:
                parsed_actions = self._parse_tool_calls_to_actions(tool_calls)
                if parsed_actions:
                    action_source = "tool_calls"
                    print(f"        来源: API tool_calls")
            
            # 所有方式都没解析到动作
            if not parsed_actions:
                print(f"        未解析到任何动作")
                # 检查是否有工具调用的痕迹（残片中包含已知函数名）
                has_action_hint = any(f"{fn}>" in content_text for fn in KNOWN_FUNCTIONS)
                if not has_action_hint and not tool_calls:
                    print(f"        -> 模型返回纯文本，视为任务结束")
                    step_log["result"] = "done_text_only"
                    step_log["final_answer"] = content_text
                    execution_log["steps"].append(step_log)
                    break
                else:
                    print(f"        -> [警告] 有工具调用痕迹但解析失败，继续下一步")
                    step_log["error"] = "parse_empty"
                    execution_log["steps"].append(step_log)
                    continue
            
            # 打印解析出的动作
            step_log["actions"] = []
            step_log["action_source"] = action_source
            for i, act in enumerate(parsed_actions):
                print(f"        动作 {i+1}: {act['function']}({act['parameters']})")
                step_log["actions"].append({"function": act["function"], "parameters": act["parameters"]})
            
            # ---- 4. 执行动作 ----
            print(f"  [4/4] 执行动作...")
            should_stop = False
            
            for act in parsed_actions:
                func_name = act['function']
                
                # 检查终止动作
                if func_name in TERMINAL_ACTIONS:
                    final_content = act['parameters'].get('content', '')
                    print(f"        >>> 终止动作: {func_name}")
                    if final_content:
                        print(f"            内容: {final_content}")
                    step_log["result"] = func_name
                    step_log["final_answer"] = final_content
                    should_stop = True
                    break
                
                # 转换为 pyautogui 代码
                pyautogui_code = seed_action_to_pyautogui(act, img_width, img_height)
                if pyautogui_code is None:
                    continue
                
                print(f"        执行: {pyautogui_code}")
                
                # 发送到 VM 执行
                try:
                    result = self.controller.execute_python_command(pyautogui_code)
                    if result:
                        print(f"        结果: {str(result)[:200]}")
                except Exception as e:
                    print(f"        [错误] 执行失败: {e}")
                    step_log.setdefault("exec_errors", []).append(str(e))
            
            execution_log["steps"].append(step_log)
            
            if should_stop:
                print(f"\n  任务结束（{step_log.get('result', 'unknown')}）")
                break
            
            # 步间等待
            elapsed = time.time() - step_start_time
            print(f"        本步总耗时: {elapsed:.1f}s，等待 {self.action_pause}s 后继续...")
            time.sleep(self.action_pause)
        
        else:
            print(f"\n  已达到最大步数 ({self.max_steps})，测试结束。")
        
        # 保存日志
        execution_log["end_time"] = datetime.now().isoformat()
        execution_log["total_steps"] = len(execution_log["steps"])
        self._save_log(execution_log)
        
        print(f"\n{'=' * 70}")
        print(f"  测试完成！共执行 {execution_log['total_steps']} 步")
        print(f"  日志已保存至: {self.output_dir}")
        print(f"{'=' * 70}")


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Seed 1.8 GUI Agent 交互测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python seed_1_8_gui_test.py --task "打开Firefox浏览器并访问百度"
  
  # 指定 VM 和步数
  python seed_1_8_gui_test.py --task "在桌面创建一个文本文件" --vm-ip 10.1.110.114 --server-port 5000 --max-steps 10
  
  # 不保存截图（只打印日志）
  python seed_1_8_gui_test.py --task "打开终端" --no-save
        """
    )
    
    # 任务参数
    parser.add_argument("--task", type=str, default=None,
                        help="任务指令（如果不指定则进入交互模式输入）")
    
    # VM 参数
    parser.add_argument("--vm-ip", type=str, default="10.1.110.114",
                        help="虚拟机 IP 地址 (默认: 10.1.110.114)")
    parser.add_argument("--server-port", type=int, default=5000,
                        help="VM Python Server 端口 (默认: 5000)")
    
    # 模型参数
    parser.add_argument("--model", type=str, default="doubao-seed-1-8-251228",
                        help="模型名称 (默认: doubao-seed-1-8-251228)")
    parser.add_argument("--api-key", type=str, default="",
                        help="API Key（默认从 api_config.py 读取 DeerAPI 配置）")
    parser.add_argument("--base-url", type=str, default="https://api.deerapi.com/v1/",
                        help="API Base URL (默认: DeerAPI)")
    parser.add_argument("--sdk", type=str, default="openai", choices=["ark", "openai"],
                        help="SDK 类型: openai (DeerAPI 兼容, 默认) 或 ark (火山引擎原生)")
    
    # 运行参数
    parser.add_argument("--max-steps", type=int, default=50,
                        help="最大执行步数 (默认: 20)")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="模型最大生成 token 数 (默认: 8192)")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="采样温度 (默认: 0.0)")
    parser.add_argument("--top-p", type=float, default=0.9,
                        help="Top-P 采样 (默认: 0.9)")
    parser.add_argument("--history-n", type=int, default=5,
                        help="保留的历史截图数量 (默认: 5)")
    parser.add_argument("--pause", type=float, default=2.0,
                        help="每步动作后等待时间/秒 (默认: 2.0)")
    parser.add_argument("--no-save", action="store_true",
                        help="不保存截图和日志到磁盘")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="输出目录（默认自动生成带时间戳的目录）")
    
    args = parser.parse_args()
    
    # 获取任务指令
    task = args.task
    if task is None:
        print("请输入任务指令（直接回车使用默认任务）:")
        task = input("> ").strip()
        if not task:
            task = "Please open the Firefox browser."
            print(f"使用默认任务: {task}")
    
    # 创建测试器并运行
    tester = Seed18GUITester(
        vm_ip=args.vm_ip,
        server_port=args.server_port,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        sdk=args.sdk,
        max_steps=args.max_steps,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        history_n=args.history_n,
        action_pause=args.pause,
        save_screenshots=not args.no_save,
        output_dir=args.output_dir,
    )
    
    tester.run(task)


if __name__ == "__main__":
    main()
