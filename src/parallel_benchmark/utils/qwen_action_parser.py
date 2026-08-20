"""
Qwen Action Parser - 解析 Qwen3-VL 的 function calling 输出并转换为 PyAutoGUI 代码
"""
import json
import re
from typing import Dict, Optional, Tuple
from PIL import Image
from io import BytesIO
import base64

# 导入已有的 hotkey 生成函数
try:
    from .action_parser import generate_pyautogui_hotkey_code
except ImportError:
    from action_parser import generate_pyautogui_hotkey_code


def parse_qwen_response(response_text: str) -> Optional[Dict]:
    """
    从 Qwen 响应中解析 tool_call
    
    Args:
        response_text: Qwen 模型的响应文本，包含 <tool_call>...</tool_call>
    
    Returns:
        dict: 解析后的 action 字典，格式如：
        {
            "name": "computer_use",
            "arguments": {
                "action": "left_click",
                "coordinate": [500, 300]
            }
        }
        如果没有 tool_call，返回 None
    """
    try:
        # 使用正则提取 <tool_call> 和 </tool_call> 之间的内容
        pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'
        match = re.search(pattern, response_text, re.DOTALL)
        
        if not match:
            print("No tool_call found in response")
            return None
        
        tool_call_str = match.group(1).strip()
        
        # 解析 JSON
        action_dict = json.loads(tool_call_str)
        
        return action_dict
    
    except json.JSONDecodeError as e:
        print(f"Failed to parse tool_call JSON: {e}")
        print(f"Content: {tool_call_str if 'tool_call_str' in locals() else 'N/A'}")
        return None
    except Exception as e:
        print(f"Error parsing qwen response: {e}")
        return None


def qwen_action_to_pyautogui(
    action_dict: Dict,
    image_width: int,
    image_height: int,
    resized_width: int = 1000,
    resized_height: int = 1000
) -> str:
    """
    将 Qwen 的 action 转换为 PyAutoGUI 代码
    
    Args:
        action_dict: 解析后的 action 字典
        image_width: 原始图像宽度
        image_height: 原始图像高度
        resized_width: Qwen 使用的图像宽度（默认 1000）
        resized_height: Qwen 使用的图像高度（默认 1000）
    
    Returns:
        str: 可执行的 PyAutoGUI 代码字符串
    """
    if not action_dict or 'arguments' not in action_dict:
        return "# No valid action"
    
    arguments = action_dict['arguments']
    action = arguments.get('action', '')
    
    pyautogui_code = "import pyautogui\nimport time\npyautogui.FAILSAFE = False\n"
    
    # 坐标转换函数
    def convert_coordinate(coord_relative: list) -> Tuple[int, int]:
        """将 Qwen 的相对坐标 (0-1000) 转换为实际像素坐标"""
        x_relative, y_relative = coord_relative
        # 先映射到 resized 图像
        x_resized = x_relative / 1000 * resized_width
        y_resized = y_relative / 1000 * resized_height
        # 再映射到原始图像
        x_actual = int(x_resized / resized_width * image_width)
        y_actual = int(y_resized / resized_height * image_height)
        return x_actual, y_actual
    
    # 根据不同的 action 类型生成代码
    if action == "left_click":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.click({x}, {y}, button='left')\n"
    
    elif action == "right_click":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.click({x}, {y}, button='right')\n"
    
    elif action == "middle_click":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.click({x}, {y}, button='middle')\n"
    
    elif action == "double_click":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.doubleClick({x}, {y})\n"
    
    elif action == "triple_click":
        # Qwen 的 triple_click 映射为 double_click
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.doubleClick({x}, {y})\n"
    
    elif action == "mouse_move":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.moveTo({x}, {y})\n"
    
    elif action == "left_click_drag":
        coordinate = arguments.get('coordinate', [])
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.dragTo({x}, {y}, duration=0.5)\n"
    
    elif action == "key":
        # 使用 generate_pyautogui_hotkey_code 处理组合键
        keys = arguments.get('keys', [])
        if keys:
            hotkey_code = generate_pyautogui_hotkey_code(keys)
            pyautogui_code += hotkey_code + "\n"
    
    elif action == "type":
        text = arguments.get('text', '')
        if text:
            # 转义单引号
            text = text.replace("'", "\\'")
            pyautogui_code += f"pyautogui.write('{text}', interval=0.1)\n"
    
    elif action == "scroll":
        pixels = arguments.get('pixels', 0)
        coordinate = arguments.get('coordinate')
        if coordinate:
            x, y = convert_coordinate(coordinate)
            pyautogui_code += f"pyautogui.scroll({pixels}, x={x}, y={y})\n"
        else:
            pyautogui_code += f"pyautogui.scroll({pixels})\n"
    
    elif action == "hscroll":
        # 水平滚动映射为普通滚动
        pixels = arguments.get('pixels', 0)
        pyautogui_code += f"pyautogui.scroll({pixels})\n"
    
    elif action == "wait":
        time_sec = arguments.get('time', 2)
        return "WAIT"  # 返回特殊标记
    
    elif action == "terminate":
        status = arguments.get('status', 'success')
        return "DONE"  # 返回特殊标记
    
    elif action == "answer":
        # answer 动作不需要执行，只是返回答案
        return "DONE"
    
    else:
        pyautogui_code += f"# Unknown action: {action}\n"
    
    return pyautogui_code


def build_qwen_messages(screenshot_bytes: bytes, instruction: str, computer_use_tool) -> list:
    """
    构建 Qwen function calling 格式的消息
    
    Args:
        screenshot_bytes: 截图的字节数据
        instruction: 用户指令
        computer_use_tool: ComputerUse 工具实例
    
    Returns:
        list: 完整的 messages 列表
    """
    from qwen_agent.llm.fncall_prompts.nous_fncall_prompt import (
        NousFnCallPrompt,
        Message,
        ContentItem,
    )
    
    # 将截图转换为 base64
    base64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
    
    # 构建系统消息（包含工具定义）
    system_message = NousFnCallPrompt().preprocess_fncall_messages(
        messages=[
            Message(role="system", content=[ContentItem(text="You are a helpful assistant.")]),
        ],
        functions=[computer_use_tool.function],
        lang=None,
    )
    system_message = system_message[0].model_dump()
    
    # 构建完整的消息列表
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": msg["text"]} for msg in system_message["content"]
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                },
                {"type": "text", "text": instruction},
            ],
        }
    ]
    
    return messages


def extract_qwen_coordinates(action_dict: Dict, resized_width: int = 1000, resized_height: int = 1000) -> list:
    """
    从 Qwen action 中提取坐标（用于可视化）
    
    Args:
        action_dict: Qwen 的 action 字典
        resized_width: 缩放后的宽度
        resized_height: 缩放后的高度
    
    Returns:
        list: 坐标列表 [(x1, y1), (x2, y2), ...]
    """
    if not action_dict or 'arguments' not in action_dict:
        return []
    
    arguments = action_dict['arguments']
    coordinates = []
    
    # 提取 coordinate 字段
    if 'coordinate' in arguments and arguments['coordinate']:
        coord = arguments['coordinate']
        # Qwen 返回的是相对坐标 (0-1000)
        x = int(coord[0] / 1000 * resized_width)
        y = int(coord[1] / 1000 * resized_height)
        coordinates.append((x, y))
    
    # 如果是 drag，还要提取 coordinate2
    if 'coordinate2' in arguments and arguments['coordinate2']:
        coord2 = arguments['coordinate2']
        x2 = int(coord2[0] / 1000 * resized_width)
        y2 = int(coord2[1] / 1000 * resized_height)
        coordinates.append((x2, y2))
    
    return coordinates

