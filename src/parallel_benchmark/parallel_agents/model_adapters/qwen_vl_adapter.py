"""
Qwen VL Adapter - Qwen 视觉语言模型适配器
使用 OpenAI 兼容 API 格式调用 Qwen VL 模型
采用 OSWorld 方案: 动态 smart_resize + 相对坐标系统
"""
import sys
import os
import base64
import math
from io import BytesIO
from typing import Dict, List, Optional, Any, Tuple
from PIL import Image
from openai import OpenAI

# 添加路径以导入工具
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from model_adapters.base_adapter import BaseModelAdapter
except ImportError:
    from .base_adapter import BaseModelAdapter

from prompts.qwen_gui_agent_prompt import (
    get_qwen_computer_use_tool, 
    QWEN_SYSTEM_PROMPT, 
    QWEN_USER_PROMPT_FIRST, 
    QWEN_USER_PROMPT_CONTINUE,
    QWEN_DISPLAY_WIDTH,
    QWEN_DISPLAY_HEIGHT,
    convert_normalized_to_pixel
)

# 定义常量
FINISH_WORD = "finished"
WAIT_WORD = "wait"

# OSWorld 图像处理参数
IMAGE_FACTOR = 32
MAX_PIXELS = 16 * 16 * 4 * 12800  # OSWorld 标准
MIN_PIXELS = 100 * 28 * 28
MAX_RATIO = 200


def ceil_by_factor(value: float, factor: int) -> int:
    """向上取整到 factor 的倍数"""
    return math.ceil(value / factor) * factor


def floor_by_factor(value: float, factor: int) -> int:
    """向下取整到 factor 的倍数"""
    return math.floor(value / factor) * factor


def round_by_factor(value: int, factor: int) -> int:
    """四舍五入到 factor 的倍数"""
    return round(value / factor) * factor


def smart_resize(
    height: int,
    width: int,
    factor: int = IMAGE_FACTOR,
    min_pixels: int = MIN_PIXELS,
    max_pixels: int = MAX_PIXELS
) -> Tuple[int, int]:
    """
    智能调整图像大小 (OSWorld 标准实现)
    
    确保:
    1. 宽高都能被 factor 整除
    2. 总像素数在 [min_pixels, max_pixels] 范围内
    3. 尽可能保持原始宽高比
    
    Args:
        height: 原始高度
        width: 原始宽度
        factor: 对齐因子 (默认32)
        min_pixels: 最小像素数
        max_pixels: 最大像素数
    
    Returns:
        (调整后的高度, 调整后的宽度)
    """
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(
            f"宽高比必须小于 {MAX_RATIO}, 当前为 {max(height, width) / min(height, width)}"
        )
    
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)
    
    return h_bar, w_bar


def parse_qwen_response(response) -> Optional[Dict]:
    """
    解析 Qwen 模型的响应（从 tool_calls 中提取动作）
    
    Args:
        response: API response 对象
    
    Returns:
        解析后的动作字典，如果没有找到则返回 None
        包含 'reasoning' 字段（如果模型提供了的话）
    """
    try:
        message = response.choices[0].message
        
        # 检查是否有 tool_calls
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            if tool_call.function.name == "computer_use":
                import json
                arguments = json.loads(tool_call.function.arguments)
                
                # 提取 reasoning（如果存在）
                reasoning = arguments.get("reasoning", "")
                
                return {
                    "name": "computer_use",
                    "arguments": arguments,
                    "reasoning": reasoning  # 添加 reasoning 字段
                }
        
        # 如果没有 tool_calls，检查文本内容是否表示完成
        if message.content:
            content_lower = message.content.lower()
            if any(keyword in content_lower for keyword in ["done", "finished", "completed", "task complete"]):
                return {
                    "name": "computer_use",
                    "arguments": {"action": "terminate", "status": "success"},
                    "reasoning": message.content
                }
        
        print("No tool_calls found in Qwen response")
        return None
        
    except Exception as e:
        print(f"Error parsing Qwen response: {e}")
        return None


def qwen_action_to_pyautogui(action_dict: Dict, screen_width: int, screen_height: int) -> str:
    """
    将 Qwen 返回的动作转换为 pyautogui 代码
    
    Qwen 使用 0-1000 的相对坐标系统（官方标准），需要转换为实际像素坐标。
    
    支持两种格式：
    1. Qwen 内置格式: {"action": "left_click", "coordinate": [x, y]}
    2. 我们的格式: {"action": "click", "x": x, "y": y, "button": "left"}
    
    Args:
        action_dict: 动作字典 {"name": "computer_use", "arguments": {...}}
        screen_width: 实际屏幕宽度（像素）
        screen_height: 实际屏幕高度（像素）
    
    Returns:
        pyautogui 代码字符串
    """
    if not action_dict or "arguments" not in action_dict:
        return ""
    
    args = action_dict["arguments"]
    action = args.get("action", "")
    
    # 坐标转换函数：0-1000 相对坐标 → 实际像素坐标
    def convert_coord(x, y):
        """将 0-1000 相对坐标转换为实际像素坐标"""
        pixel_x = int(x * screen_width / 1000)
        pixel_y = int(y * screen_height / 1000)
        return pixel_x, pixel_y
    
    # 提取坐标 - 支持两种格式
    def get_coordinates():
        """从 args 中提取坐标，支持 coordinate 或 x/y 格式
        返回相对坐标（0-1000），需要后续转换
        """
        if "coordinate" in args:
            coord = args["coordinate"]
            if isinstance(coord, list) and len(coord) >= 2:
                return int(coord[0]), int(coord[1])
        
        # 处理 x 字段：可能是数组 [x, y] 或单个数字
        x_val = args.get("x", 0)
        y_val = args.get("y", 0)
        
        if isinstance(x_val, list) and len(x_val) >= 2:
            # Qwen 返回 "x": [287, 260] 格式
            return int(x_val[0]), int(x_val[1])
        
        return int(x_val), int(y_val)
    
    # 处理 Qwen 内置的 action 名称映射
    # left_click -> click (button=left)
    # right_click -> click (button=right)
    # middle_click -> click (button=middle)
    button = "left"
    if action == "left_click":
        action = "click"
        button = "left"
    elif action == "right_click":
        action = "click"
        button = "right"
    elif action == "middle_click":
        action = "click"
        button = "middle"
    else:
        button = args.get("button", "left")
    
    if action == "click":
        x, y = get_coordinates()
        pixel_x, pixel_y = convert_coord(x, y)
        print(f"[DEBUG] Qwen coordinate conversion: ({x}, {y}) → ({pixel_x}, {pixel_y}), screen: {screen_width}x{screen_height}")
        return f"pyautogui.click({pixel_x}, {pixel_y}, button='{button}')"
    
    elif action == "double_click":
        x, y = get_coordinates()
        pixel_x, pixel_y = convert_coord(x, y)
        return f"pyautogui.doubleClick({pixel_x}, {pixel_y})"
    
    elif action == "type":
        text = args.get("text", "")
        # 转义特殊字符
        text = text.replace("\\", "\\\\").replace("'", "\\'")
        return f"pyautogui.typewrite('{text}', interval=0.05)"
    
    elif action == "key":
        key = args.get("key", "")
        # 处理组合键
        if "+" in key:
            keys = key.split("+")
            return f"pyautogui.hotkey({', '.join([repr(k.strip()) for k in keys])})"
        else:
            return f"pyautogui.press('{key}')"
    
    elif action == "scroll":
        x, y = get_coordinates() if "coordinate" in args or "x" in args else (500, 500)
        pixel_x, pixel_y = convert_coord(x, y)
        direction = args.get("direction", "down")
        amount = args.get("amount", 3)
        
        if direction in ["up", "down"]:
            scroll_amount = amount if direction == "up" else -amount
            return f"pyautogui.scroll({scroll_amount}, {pixel_x}, {pixel_y})"
        else:
            # 左右滚动
            scroll_amount = amount if direction == "right" else -amount
            return f"pyautogui.hscroll({scroll_amount}, {pixel_x}, {pixel_y})"
    
    elif action == "drag":
        # drag 可能使用 start_x/start_y/end_x/end_y 或 start_coordinate/end_coordinate
        if "start_coordinate" in args and "end_coordinate" in args:
            start_coord = args["start_coordinate"]
            end_coord = args["end_coordinate"]
            start_x, start_y = int(start_coord[0]), int(start_coord[1])
            end_x, end_y = int(end_coord[0]), int(end_coord[1])
        else:
            start_x = int(args.get("start_x", 0))
            start_y = int(args.get("start_y", 0))
            end_x = int(args.get("end_x", 0))
            end_y = int(args.get("end_y", 0))
        
        pixel_start_x, pixel_start_y = convert_coord(start_x, start_y)
        pixel_end_x, pixel_end_y = convert_coord(end_x, end_y)
        return f"pyautogui.moveTo({pixel_start_x}, {pixel_start_y}); pyautogui.drag({pixel_end_x - pixel_start_x}, {pixel_end_y - pixel_start_y}, duration=0.5)"
    
    elif action == "move" or action == "mouse_move":
        x, y = get_coordinates()
        pixel_x, pixel_y = convert_coord(x, y)
        return f"pyautogui.moveTo({pixel_x}, {pixel_y})"
    
    elif action == "screenshot":
        return "# screenshot requested"
    
    elif action == "wait":
        seconds = args.get("amount", 1)
        return f"time.sleep({seconds})"
    
    elif action == "terminate":
        status = args.get("status", "success")
        text = args.get("text", "")
        if text:
            return f"DONE:{text}"
        return "DONE"
    
    elif action == "answer":
        text = args.get("text", "")
        # answer 动作用于报告结果，也视为完成
        return f"DONE:{text}"
    
    else:
        print(f"Unknown action: {action}")
        return ""


class QwenVLAdapter(BaseModelAdapter):
    """Qwen VL 模型适配器（采用 OSWorld 方案: smart_resize + 相对坐标）"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化 Qwen VL 适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        super().__init__(runtime_conf)
        self.model_type = "qwen"
        
        print(f"\n{'#'*80}")
        print(f"# QwenVLAdapter INITIALIZED - OSWorld方案: smart_resize + 相对坐标")
        print(f"{'#'*80}\n")
        
        # 使用相对坐标系统 (0-999)
        self.coordinate_type = "relative"
        self.display_width = 999  # 相对坐标范围
        self.display_height = 999
        
        # 添加 action_parse_res_factor 用于解析响应
        self.action_parse_res_factor = 1000
        
        # 是否启用高清图像模式
        self.enable_high_resolution = runtime_conf.get("enable_high_resolution", True)
    
    def build_messages(
        self,
        instruction: str,
        history_images: List[bytes],
        history_responses: List[str],
        current_screenshot: bytes
    ) -> List[Dict]:
        """
        构建 Qwen 模型的消息格式
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表
            history_responses: 历史响应列表
            current_screenshot: 当前截图
        
        Returns:
            消息列表和图像信息的元组 (messages, image_info)
        """
        # 获取图像实际尺寸（用于坐标转换）
        current_image = Image.open(BytesIO(current_screenshot))
        original_width, original_height = current_image.size
        
        print(f"[IMAGE] Original size: {original_width}x{original_height}")
        
        # **临时测试: 不使用 smart_resize,直接发送原始图像**
        # 看看是否 resize 导致了坐标偏差
        USE_RESIZE = False  # 改为 False 测试
        
        if USE_RESIZE:
            # OSWorld 方案: 使用 smart_resize 动态调整大小
            resized_height, resized_width = smart_resize(
                height=original_height,
                width=original_width,
                factor=IMAGE_FACTOR,
                max_pixels=MAX_PIXELS,
                min_pixels=MIN_PIXELS
            )
            
            print(f"[IMAGE] Resized to: {resized_width}x{resized_height} (no padding)")
            
            # 缩放图像（保持宽高比，无padding）
            resized_image = current_image.resize(
                (resized_width, resized_height),
                Image.Resampling.LANCZOS
            )
        else:
            # 直接使用原始图像,不 resize
            print(f"[IMAGE] Using original image (NO RESIZE)")
            resized_image = current_image
            resized_width = original_width
            resized_height = original_height
        
        # 转换为 base64
        buffer = BytesIO()
        resized_image.save(buffer, format='PNG')
        base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        # 保存图像信息，用于坐标转换
        image_info = {
            "original_width": original_width,
            "original_height": original_height,
            "processed_width": resized_width,
            "processed_height": resized_height,
            "coordinate_type": self.coordinate_type
        }
        
        # 构建系统消息（使用 1000x1000 归一化坐标系统）
        system_message = {
            "role": "system",
            "content": QWEN_SYSTEM_PROMPT
        }
        
        # 构建消息列表
        messages = [system_message]
        
        # 添加历史消息（如果有）
        if history_responses:
            recent_history = history_responses[-min(len(history_responses), self.history_n * 2):]
            for i, response in enumerate(recent_history[::2]):
                messages.append({
                    "role": "user",
                    "content": instruction if i == 0 else "Continue the task."
                })
                resp_idx = i * 2
                if resp_idx < len(recent_history):
                    history_resp = recent_history[resp_idx]
                    if isinstance(history_resp, str):
                        content_str = history_resp
                    else:
                        try:
                            content_str = history_resp.choices[0].message.content or "Action executed."
                        except:
                            content_str = str(history_resp)[:500]
                    messages.append({
                        "role": "assistant",
                        "content": content_str
                    })
        
        # 添加当前用户消息（带截图）
        user_text = QWEN_USER_PROMPT_FIRST.format(instruction=instruction) if not history_responses else QWEN_USER_PROMPT_CONTINUE.format(instruction=instruction)
        
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
        messages.append(current_user_message)
        
        # 返回消息和图像信息
        return messages, image_info
    
    def call_model(
        self,
        messages: Any,
        vlm_client: OpenAI,
        model_name: str,
        temperature: float = 0.0,
        **kwargs
    ) -> str:
        """
        调用 Qwen VL API（使用 OpenAI 兼容格式）
        像 Claude 一样直接返回文本内容，不使用 Function Calling
        
        Args:
            messages: 消息列表（可能是元组）
            vlm_client: OpenAI 客户端
            model_name: 模型名称
            temperature: 温度参数
            **kwargs: 其他参数
        
        Returns:
            模型响应文本
        """
        # 提取 messages 和 image_info
        if isinstance(messages, tuple):
            messages_list, image_info = messages
        else:
            messages_list = messages
            image_info = None
        
        # 调用 API - 不使用 tools 参数
        response = vlm_client.chat.completions.create(
            model=model_name,
            messages=messages_list,
            temperature=temperature,
            max_tokens=kwargs.get("max_tokens", 2000),
            timeout=120,
        )
        
        # 提取响应文本
        prediction = response.choices[0].message.content.strip()
        return prediction
    
    def parse_response(
        self,
        response_text: str,
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Any] = None
    ) -> Optional[List[Dict]]:
        """
        解析 Qwen 模型响应（文本格式）
        将 JSON 格式转换为标准 Thought/Action 格式后调用 parse_action_to_structure_output
        
        Args:
            response_text: 模型响应文本（JSON 格式）
            image_width: 截图实际宽度（像素）
            image_height: 截图实际高度（像素）
            image_info: 图像信息字典
            last_image: 最后一张图片（用于获取实际尺寸）
        
        Returns:
            解析后的动作字典列表
        """
        # 使用 1000x1000 作为归一化尺寸
        origin_resized_height = self.display_height
        origin_resized_width = self.display_width
        
        try:
            # 导入必要的模块
            import json
            import re
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from utils.action_parser import parse_action_to_structure_output
            
            # 预处理：移除 markdown 代码块标记（Qwen 返回 ```json ... ``` 格式）
            cleaned_text = re.sub(r'^```json\s*', '', response_text.strip())
            cleaned_text = re.sub(r'\s*```$', '', cleaned_text)
            
            # 尝试解析 JSON
            try:
                json_data = json.loads(cleaned_text)
                
                # 将 JSON 转换为 Thought/Action 格式
                reasoning = json_data.get("reasoning", "")
                action = json_data.get("action", "")
                
                # 构建标准格式文本
                formatted_text = f"Thought: {reasoning}\nAction: "
                
                # 根据不同动作类型构建 Action 字符串
                # 注意：坐标转换在 response_to_code 中进行
                if action == "terminate":
                    status = json_data.get("status", "success")
                    summary = json_data.get("summary", json_data.get("message", ""))
                    formatted_text += f"DONE"
                    if summary:
                        formatted_text += f" # {summary}"
                
                elif action in ["left_click", "right_click", "middle_click", "click"]:
                    coordinate = json_data.get("coordinate", [500, 500])
                    if isinstance(coordinate, list) and len(coordinate) >= 2:
                        # 直接使用模型返回的 1000 空间坐标
                        formatted_text += f"click(start_box='({coordinate[0]}, {coordinate[1]})')"
                
                elif action == "double_click":
                    coordinate = json_data.get("coordinate", [500, 500])
                    if isinstance(coordinate, list) and len(coordinate) >= 2:
                        # 直接使用模型返回的 1000 空间坐标
                        formatted_text += f"double_click(start_box='({coordinate[0]}, {coordinate[1]})')"
                
                elif action == "type":
                    text = json_data.get("text", "")
                    formatted_text += f"type(text='{text}')"
                
                elif action == "key":
                    key = json_data.get("key", "")
                    formatted_text += f"key(key='{key}')"
                
                elif action == "scroll":
                    direction = json_data.get("direction", "down")
                    amount = json_data.get("amount", 3)
                    coordinate = json_data.get("coordinate", [500, 500])
                    if isinstance(coordinate, list) and len(coordinate) >= 2:
                        # 直接使用模型返回的 1000 空间坐标
                        formatted_text += f"scroll(start_box='({coordinate[0]}, {coordinate[1]})', direction='{direction}', amount={amount})"
                
                else:
                    # 未知动作，尝试直接使用原始 JSON
                    formatted_text = cleaned_text
                
                print(f"[DEBUG] Converted JSON to format: {formatted_text}")
                cleaned_text = formatted_text
                
            except json.JSONDecodeError:
                # 不是 JSON 格式，可能已经是标准格式，直接使用
                pass
            
            parsed_responses = parse_action_to_structure_output(
                cleaned_text,
                factor=self.action_parse_res_factor,
                origin_resized_height=origin_resized_height,
                origin_resized_width=origin_resized_width,
                model_type=self.model_type,
                max_pixels=self.max_pixels,
                min_pixels=self.min_pixels
            )
            return parsed_responses
        except Exception as e:
            print(f"Error when parsing response: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _generate_simple_pyautogui_code(self, parsed_response, screen_width, screen_height, processed_width=None, processed_height=None):
        """
        简单直接地生成 pyautogui 代码,不经过 action_parser 的复杂转换
        
        Args:
            parsed_response: 单个解析后的 action
            screen_width: 实际屏幕宽度(像素) - 用于执行
            screen_height: 实际屏幕高度(像素) - 用于执行
            processed_width: 模型看到的图像宽度 - 用于坐标映射
            processed_height: 模型看到的图像高度 - 用于坐标映射
        
        Returns:
            pyautogui 代码字符串
        """
        import ast
        
        # 如果没有提供 processed 尺寸,使用 screen 尺寸
        if processed_width is None:
            processed_width = screen_width
        if processed_height is None:
            processed_height = screen_height
        
        action_type = parsed_response.get("action_type", "")
        action_inputs = parsed_response.get("action_inputs", {})
        
        print(f"\n[SIMPLE CODEGEN] Action: {action_type}")
        print(f"[SIMPLE CODEGEN] Inputs: {action_inputs}")
        print(f"[SIMPLE CODEGEN] Screen: {screen_width}x{screen_height}, Processed: {processed_width}x{processed_height}")
        
        # 处理 click 和 double_click 动作
        if action_type in ["click", "double_click"]:
            start_box = action_inputs.get("start_box", "")
            try:
                coords = ast.literal_eval(start_box)
                # coords 是归一化坐标 [0-1],由 action_parser 处理过(除以1000)
                # 这些坐标是基于模型看到的 processed 图像的
                
                # 先映射到 processed 图像的像素坐标
                processed_x = coords[0] * processed_width
                processed_y = coords[1] * processed_height
                
                # 再按比例转换到实际屏幕坐标
                x = int(processed_x * screen_width / processed_width)
                y = int(processed_y * screen_height / processed_height)
                
                print(f"[SIMPLE CODEGEN] Normalized: {coords[:2]}")
                print(f"[SIMPLE CODEGEN] Processed pixels: ({processed_x:.1f}, {processed_y:.1f})")
                print(f"[SIMPLE CODEGEN] Screen pixels (raw): ({x}, {y})")
                
                # **不添加偏移,保持原始坐标**
                # 重试机制将在 GUI Agent 层面处理
                
                # double_click 需要 clicks=2
                if action_type == "double_click":
                    return f"pyautogui.click({x}, {y}, button='left', clicks=2)"
                else:
                    return f"pyautogui.click({x}, {y}, button='left')"
            except Exception as e:
                print(f"[ERROR] Failed to parse click coords: {e}")
                return ""
        
        # 处理 type 动作
        elif action_type == "type":
            text = action_inputs.get("text", action_inputs.get("content", ""))
            if text:
                # 转义特殊字符
                text_escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
                print(f"[SIMPLE CODEGEN] Typing: {text[:50]}...")
                return f"pyautogui.write('{text_escaped}', interval=0.05)"
            return ""
        
        # 处理 key 动作(快捷键)
        elif action_type == "key":
            key = action_inputs.get("text", "")
            if key:
                # 处理组合键,如 "ctrl+s"
                keys = [k.strip() for k in key.lower().split("+")]
                if len(keys) > 1:
                    hotkey_str = ", ".join(f"'{k}'" for k in keys)
                    print(f"[SIMPLE CODEGEN] Hotkey: {key}")
                    return f"pyautogui.hotkey({hotkey_str})"
                else:
                    print(f"[SIMPLE CODEGEN] Single key: {key}")
                    return f"pyautogui.press('{key}')"
            return ""
        
        # 处理 scroll 动作
        elif action_type == "scroll":
            direction = action_inputs.get("text", "down")
            clicks = -3 if direction == "down" else 3
            print(f"[SIMPLE CODEGEN] Scroll: {direction}")
            return f"pyautogui.scroll({clicks})"
        
        print(f"[WARNING] Unknown action type: {action_type}")
        return ""
    
    def response_to_code(
        self,
        parsed_responses: List[Dict],
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Any] = None
    ) -> str:
        """
        将解析后的响应转换为 pyautogui 代码
        支持智能重试: 如果是 click/double_click,生成带重试的代码
        
        Args:
            parsed_responses: 解析后的响应列表
            image_width: 截图实际宽度（像素）
            image_height: 截图实际高度（像素）
            image_info: 图像信息字典（包含 padding_info）
            last_image: 最后一张图片
        
        Returns:
            pyautogui 代码字符串
        """
        print(f"\n{'='*80}")
        print(f"[QWEN VL ADAPTER] response_to_code() CALLED!!!")
        print(f"  Image size: {image_width}x{image_height}")
        print(f"  Parsed responses: {len(parsed_responses)}")
        print(f"{'='*80}\n")
        
        # 导入转换函数
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        from utils.action_parser import parsing_response_to_pyautogui_code
        
        # 获取图像信息
        original_width = image_info.get("original_width", image_width) if image_info else image_width
        original_height = image_info.get("original_height", image_height) if image_info else image_height
        processed_width = image_info.get("processed_width", image_width) if image_info else image_width
        processed_height = image_info.get("processed_height", image_height) if image_info else image_height
        
        print(f"[COORD] Original: {original_width}x{original_height}")
        print(f"[COORD] Processed: {processed_width}x{processed_height}")
        
        pyautogui_code = ""
        
        for parsed_response in parsed_responses:
            if "action_type" in parsed_response:
                action_type = parsed_response["action_type"]
                if action_type == FINISH_WORD:
                    return "DONE"
                elif action_type == WAIT_WORD:
                    return "WAIT"
            
            # **直接在这里生成 pyautogui 代码,绕过 action_parser 的复杂坐标转换**
            # 注意: 模型看到的是 processed 图像,但点击要用 original 尺寸
            # 所以需要按比例转换
            code = self._generate_simple_pyautogui_code(
                parsed_response,
                original_width,
                original_height,
                processed_width,
                processed_height
            )
            if code:
                # 不再自动添加重试,让模型在下一轮观察截图后自己决定是否调整
                pyautogui_code += code + "\n"
        
        return pyautogui_code.strip()
    
    def _add_retry_logic(self, click_code: str, action_type: str) -> str:
        """
        为点击动作添加重试逻辑
        
        思路:
        1. 执行原始点击
        2. 等待0.5秒
        3. 如果需要,在附近位置重试
        
        Args:
            click_code: 原始的 pyautogui.click() 代码
            action_type: click 或 double_click
        
        Returns:
            带重试逻辑的代码字符串
        """
        import re
        
        # 从代码中提取坐标
        match = re.search(r'pyautogui\.click\((\d+),\s*(\d+)', click_code)
        if not match:
            return click_code
        
        x = int(match.group(1))
        y = int(match.group(2))
        clicks = 2 if action_type == "double_click" else 1
        
        # 重试偏移量 (基于观察到的Qwen-VL-Max偏差模式)
        retry_offsets = [
            (-50, 10),   # 向左50,向下10
            (-100, 20),  # 向左100,向下20
            (-80, 40),   # 向左80,向下40
        ]
        
        # 生成带重试的代码
        retry_code = f"""# 主点击尝试
pyautogui.click({x}, {y}, button='left', clicks={clicks})
time.sleep(0.5)
"""
        
        # 添加注释说明启用了重试
        retry_code = f"# [AUTO-RETRY ENABLED] 原始坐标: ({x}, {y})\n" + retry_code
        
        return retry_code.strip()
