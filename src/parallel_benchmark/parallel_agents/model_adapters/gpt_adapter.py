"""
GPT Adapter - GPT-5 模型适配器
使用 OpenAI Function Calling 格式调用 GPT-5 的 computer_use 工具
"""
import sys
import os
import base64
from io import BytesIO
from typing import Dict, List, Optional, Any
from PIL import Image
from openai import OpenAI

# 添加路径以导入工具
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from model_adapters.base_adapter import BaseModelAdapter
except ImportError:
    from .base_adapter import BaseModelAdapter

from prompts.gpt_gui_agent_prompt import get_computer_use_tool, SYSTEM_PROMPT, USER_PROMPT_FIRST, USER_PROMPT_CONTINUE
from utils.gpt_action_parser import parse_gpt_response, gpt_action_to_pyautogui


class GPTAdapter(BaseModelAdapter):
    """GPT-5 模型适配器（使用 Function Calling）"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化 GPT 适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        super().__init__(runtime_conf)
        self.model_type = "gpt"
        
        # GPT 使用图片的真实分辨率（工具定义将在调用时动态创建）
    
    def build_messages(
        self,
        instruction: str,
        history_images: List[bytes],
        history_responses: List[str],
        current_screenshot: bytes
    ) -> List[Dict]:
        """
        构建 GPT 模型的消息格式（OpenAI Function Calling）
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表
            history_responses: 历史响应列表
            current_screenshot: 当前截图
        
        Returns:
            消息列表和图像信息的元组 (messages, image_info)
        """
        # 将当前截图转换为 base64
        base64_image = base64.b64encode(current_screenshot).decode('utf-8')
        
        # 获取图像尺寸（使用真实分辨率）
        current_image = Image.open(BytesIO(current_screenshot))
        original_width, original_height = current_image.size
        
        # 构建系统消息（使用真实分辨率）
        system_message = {
            "role": "system",
            "content": SYSTEM_PROMPT.format(width=original_width, height=original_height)
        }
        
        # 构建消息列表
        messages = [system_message]
        
        # 添加历史消息（如果有）
        if history_responses:
            # 简化历史：只保留最后几轮的文本摘要，避免消息过长
            recent_history = history_responses[-min(len(history_responses), self.history_n * 2):]
            for i, response in enumerate(recent_history[::2]):  # 每两个响应一轮
                messages.append({
                    "role": "user",
                    "content": instruction if i == 0 else "Continue the task."
                })
                # 找到对应的助手响应
                resp_idx = i * 2
                if resp_idx < len(recent_history):
                    # 将历史响应转换为字符串（可能是 API response 对象或字符串）
                    history_resp = recent_history[resp_idx]
                    if isinstance(history_resp, str):
                        content_str = history_resp
                    else:
                        # GPT API response 对象，提取文本内容
                        try:
                            content_str = history_resp.choices[0].message.content or "Action executed."
                        except:
                            content_str = str(history_resp)[:500]  # 截断避免过长
                    messages.append({
                        "role": "assistant",
                        "content": content_str
                    })
        
        # 添加当前用户消息（带截图）
        user_text = USER_PROMPT_FIRST.format(instruction=instruction) if not history_responses else USER_PROMPT_CONTINUE.format(instruction=instruction)
        
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
        
        # 返回消息和图像信息（使用真实分辨率，无需缩放）
        image_info = {
            "original_width": original_width,
            "original_height": original_height,
            "resized_width": original_width,
            "resized_height": original_height
        }
        
        return messages, image_info
    
    def call_model(
        self,
        messages: Any,
        vlm_client: OpenAI,
        model_name: str,
        temperature: float = 0.0,
        **kwargs
    ) -> Any:
        """
        调用 GPT 模型 API（使用 Function Calling）
        
        Args:
            messages: 消息列表（可能是元组 (messages, image_info)）
            vlm_client: OpenAI 客户端
            model_name: 模型名称
            temperature: 温度参数
            **kwargs: 其他参数
        
        Returns:
            完整的 API response 对象（而非仅文本，以便解析 tool_calls）
        """
        # 提取 messages 和 image_info
        if isinstance(messages, tuple):
            messages_list, image_info = messages
        else:
            messages_list = messages
            image_info = None
        
        # 使用真实分辨率动态创建工具定义
        if image_info:
            display_width = image_info.get("original_width", 1920)
            display_height = image_info.get("original_height", 1080)
        else:
            display_width = 1920
            display_height = 1080
        
        # GPT-5 使用真实分辨率坐标系统（与 system prompt 一致）
        computer_use_tool = get_computer_use_tool(
            display_width_px=display_width,
            display_height_px=display_height
        )
        
        # 调用 API（使用 Function Calling），设置超时防止卡住
        response = vlm_client.chat.completions.create(
            model=model_name,
            messages=messages_list,
            tools=[computer_use_tool],
            tool_choice="auto",
            temperature=temperature,
            max_tokens=kwargs.get("max_tokens", 2000),
            timeout=120,  # 120秒超时
        )
        
        # 返回完整的 response 对象（不只是文本）
        return response
    
    def parse_response(
        self,
        response: Any,
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Any] = None
    ) -> Optional[List[Dict]]:
        """
        解析 GPT 模型响应（从 tool_calls 中提取动作）
        
        Args:
            response: API response 对象（不是文本）
            image_width: 截图宽度
            image_height: 截图高度
            image_info: 图像信息字典
        
        Returns:
            解析后的动作字典列表
        """
        # 如果是字符串（某些情况下可能只传文本），尝试从中提取内容
        if isinstance(response, str):
            # 无法从纯文本中提取 tool_calls，返回 None
            print("Warning: GPT response is a string, expected response object with tool_calls")
            return None
        
        # 解析 tool_calls
        action_dict = parse_gpt_response(response)
        
        if action_dict is None:
            # 没有 tool_calls，检查是否有文本内容表明完成
            try:
                message = response.choices[0].message
                if message.content and any(keyword in message.content.lower() for keyword in ["done", "finished", "completed"]):
                    return [{"arguments": {"action": "terminate", "status": "success"}}]
            except:
                pass
            return None
        
        # 附加图像信息用于后续处理
        if image_info:
            action_dict["_image_info"] = image_info
        
        return [action_dict]  # 返回列表以保持接口一致性
    
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
        
        Args:
            parsed_responses: 解析后的动作列表
            image_width: 截图宽度
            image_height: 截图高度
            image_info: 图像信息字典
        
        Returns:
            pyautogui 代码字符串，或特殊标记("DONE", "WAIT")
        """
        if not parsed_responses:
            return ""
        
        action_dict = parsed_responses[0]
        
        # 使用真实分辨率（无需缩放）
        if image_info:
            original_width = image_info.get("original_width", image_width)
            original_height = image_info.get("original_height", image_height)
        else:
            original_width = image_width
            original_height = image_height
        
        # 检查特殊动作
        if 'arguments' in action_dict:
            arguments = action_dict['arguments']
            action = arguments.get('action', '')
            
            if action == "wait":
                return "WAIT"
            elif action in ["terminate", "answer"]:
                # 如果是 answer 动作，提取文本内容
                if action == "answer" and "text" in arguments:
                    return f"DONE:{arguments['text']}"
                return "DONE"
        
        # 转换为 pyautogui 代码
        # GPT 使用真实分辨率坐标，无需转换
        pyautogui_code = gpt_action_to_pyautogui(
            action_dict,
            image_width=original_width,
            image_height=original_height,
            resized_width=original_width,  # GPT 使用真实分辨率坐标
            resized_height=original_height
        )
        
        return pyautogui_code

