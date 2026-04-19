"""
GPT Action Parser - 解析 GPT-5 的 function calling 输出并转换为 PyAutoGUI 代码
"""
import json
import base64
from typing import Dict, Optional, Tuple, List

# 导入已有的 hotkey 生成函数
try:
    from .action_parser import generate_pyautogui_hotkey_code
except ImportError:
    from action_parser import generate_pyautogui_hotkey_code


def parse_gpt_response(response) -> Optional[Dict]:
    """
    从 GPT 响应中解析 tool_calls
    
    Args:
        response: OpenAI API 返回的 response 对象
    
    Returns:
        dict: 解析后的 action 字典，格式如：
        {
            "name": "computer_use",
            "arguments": {
                "action": "left_click",
                "coordinate": [500, 300]
            }
        }
        如果没有 tool_calls，返回 None
    """
    try:
        message = response.choices[0].message
        
        # 检查是否有 tool_calls
        if not hasattr(message, 'tool_calls') or not message.tool_calls:
            print("No tool_calls found in response")
            return None
        
        # 获取第一个 tool_call
        tool_call = message.tool_calls[0]
        
        # 解析 function 信息
        function_name = tool_call.function.name
        arguments_str = tool_call.function.arguments
        
        # 解析 arguments JSON
        arguments = json.loads(arguments_str)
        
        return {
            "name": function_name,
            "arguments": arguments
        }
    
    except json.JSONDecodeError as e:
        print(f"Failed to parse tool_call arguments JSON: {e}")
        print(f"Content: {arguments_str if 'arguments_str' in locals() else 'N/A'}")
        return None
    except Exception as e:
        print(f"Error parsing GPT response: {e}")
        import traceback
        traceback.print_exc()
        return None


def gpt_action_to_pyautogui(
    action_dict: Dict,
    image_width: int,
    image_height: int,
    resized_width: int = 1920,
    resized_height: int = 1080
) -> str:
    """
    将 GPT 的 action 转换为 PyAutoGUI 代码
    
    Args:
        action_dict: 解析后的 action 字典
        image_width: 原始图像宽度（真实分辨率）
        image_height: 原始图像高度（真实分辨率）
        resized_width: GPT 使用的坐标宽度（默认与真实分辨率一致）
        resized_height: GPT 使用的坐标高度（默认与真实分辨率一致）
    
    Returns:
        str: 可执行的 PyAutoGUI 代码字符串
    
    Note:
        GPT模型使用真实分辨率坐标系统（1920x1080），与 system prompt 一致
    """
    if not action_dict or 'arguments' not in action_dict:
        return "# No valid action"
    
    arguments = action_dict['arguments']
    action = arguments.get('action', '')
    
    pyautogui_code = "import pyautogui\nimport time\npyautogui.FAILSAFE = False\n"
    
    # 坐标转换函数（当 GPT 使用真实分辨率时，无需转换）
    def convert_coordinate(coord: list) -> Tuple[int, int]:
        """
        将 GPT 返回的坐标转换为真实像素坐标
        当 resized_width/height == image_width/height 时，坐标不变
        """
        x_input, y_input = coord
        x_actual = int(x_input / resized_width * image_width)
        y_actual = int(y_input / resized_height * image_height)
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
        # GPT 的 triple_click 映射为 double_click
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
            # 使用剪贴板复制粘贴方式，更快速可靠
            # 如果剪贴板不可用，回退到 typewrite 方式
            text_escaped = text.replace("\\", "\\\\").replace("'", "\\'")
            pyautogui_code += f"try:\n"
            pyautogui_code += f"    import pyperclip\n"
            pyautogui_code += f"    pyperclip.copy('{text_escaped}')\n"
            pyautogui_code += f"    pyautogui.hotkey('ctrl', 'v')\n"
            pyautogui_code += f"except Exception as e:\n"
            pyautogui_code += f"    # 剪贴板不可用，使用 typewrite 作为备选\n"
            pyautogui_code += f"    pyautogui.typewrite('{text_escaped}', interval=0.05)\n"
    
    elif action == "scroll":
        pixels = arguments.get('pixels', 0)
        coordinate = arguments.get('coordinate')
        # Debug to locate source adapter/prompt behavior when scroll is excessive
        print(f"[DEBUG scroll] parser_file={__file__}, pixels={pixels}, coord={coordinate}")
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


def build_gpt_messages(
    screenshot_bytes: bytes,
    instruction: str,
    computer_use_tool: dict,
    is_first_round: bool = True,
    history_messages: List[dict] = None
) -> list:
    """
    构建 GPT function calling 格式的消息
    
    Args:
        screenshot_bytes: 截图的字节数据
        instruction: 用户指令
        computer_use_tool: computer_use 工具定义
        is_first_round: 是否是第一轮
        history_messages: 历史消息列表
    
    Returns:
        list: 完整的 messages 列表
    """
    from cookbooks.gpt.gpt_computer_use import SYSTEM_PROMPT, USER_PROMPT_FIRST, USER_PROMPT_CONTINUE
    
    # 将截图转换为 base64
    base64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
    
    # 获取屏幕分辨率（从工具定义中提取）
    width = computer_use_tool.get('function', {}).get('description', '').split('Resolution: ')[1].split('x')[0] if 'Resolution:' in computer_use_tool.get('function', {}).get('description', '') else '1000'
    height = computer_use_tool.get('function', {}).get('description', '').split('x')[1].split(' pixels')[0] if 'x' in computer_use_tool.get('function', {}).get('description', '') else '1000'
    
    # 构建系统消息
    system_message = {
        "role": "system",
        "content": SYSTEM_PROMPT.format(width=width, height=height)
    }
    
    # 构建用户消息
    if is_first_round:
        user_text = USER_PROMPT_FIRST.format(instruction=instruction)
    else:
        user_text = USER_PROMPT_CONTINUE.format(instruction=instruction)
    
    current_user_message = {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{base64_image}"},
            },
            {"type": "text", "text": user_text},
        ],
    }
    
    # 构建完整的消息列表
    messages = [system_message]
    
    # 添加历史消息
    if history_messages:
        messages.extend(history_messages)
    
    # 添加当前用户消息
    messages.append(current_user_message)
    
    return messages


def extract_gpt_coordinates(action_dict: Dict, resized_width: int = 1000, resized_height: int = 1000) -> list:
    """
    从 GPT action 中提取坐标（用于可视化）
    
    Args:
        action_dict: GPT 的 action 字典
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
        # GPT 返回的是相对坐标 (0-1000)
        x = int(coord[0] / 1000 * resized_width)
        y = int(coord[1] / 1000 * resized_height)
        coordinates.append((x, y))
    
    return coordinates

