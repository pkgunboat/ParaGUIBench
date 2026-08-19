"""
Claude Adapter - Claude Sonnet 4.5 模型适配器
通过 OpenAI 兼容格式调用（支持 deerapi 等代理）
"""
import sys
import os
from io import BytesIO
from typing import Dict, List, Optional
from PIL import Image
import numpy as np
from openai import OpenAI

# 添加路径以导入工具
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from model_adapters.base_adapter import BaseModelAdapter
except ImportError:
    from .base_adapter import BaseModelAdapter
from utils.prompt import COMPUTER_USE_DOUBAO, COMPUTER_USE_GUI_AGENT
from utils.action_parser import (
    parse_action_to_structure_output,
    parsing_response_to_pyautogui_code
)
from utils.gui_agent_tools import pil_to_base64

FINISH_WORD = "finished"
WAIT_WORD = "wait"


class ClaudeAdapter(BaseModelAdapter):
    """Claude Sonnet 4.5 模型适配器（OpenAI 兼容格式）"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化 Claude 适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        super().__init__(runtime_conf)
        self.model_type = "claude"
        self.action_parse_res_factor = 1000
        self.input_swap = runtime_conf.get("input_swap", False)
    
    def build_messages(
        self,
        instruction: str,
        history_images: List[bytes],
        history_responses: List[str],
        current_screenshot: bytes
    ) -> List[Dict]:
        """
        构建 Claude 模型的消息格式（OpenAI 兼容格式）
        
        使用 OpenAI 格式：
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                {"type": "text", "text": "..."}
            ]
        }
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表
            history_responses: 历史响应列表
            current_screenshot: 当前截图
        
        Returns:
            消息列表和最后一张图片的元组 (messages, last_image)
        """
        # 使用 GUI Agent 的 prompt
        user_prompt = COMPUTER_USE_GUI_AGENT.format(
            instruction=instruction,
            language=self.language
        )
        
        # 处理图像历史
        if isinstance(history_images, bytes):
            history_images = [history_images]
        elif isinstance(history_images, np.ndarray):
            history_images = list(history_images)
        elif not isinstance(history_images, list):
            raise TypeError(f"Unidentified images type: {type(history_images)}")
        
        # 添加当前截图
        all_images = history_images + [current_screenshot]
        
        # 转换为 PIL Image 并限制数量
        images = []
        original_sizes = []  # 记录原始尺寸
        for image_bytes in all_images[-self.history_n:]:
            try:
                image = Image.open(BytesIO(image_bytes))
                if image.mode != "RGB":
                    image = image.convert("RGB")
                
                # 记录原始尺寸
                original_sizes.append((image.width, image.height))
                
                # 压缩图片以减少 API 传输开销并保持一致的坐标系
                # Claude API 可能会内部压缩，这里手动压缩到固定尺寸
                target_width = 1280
                target_height = 720
                if image.width != target_width or image.height != target_height:
                    image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
                
                images.append(image)
            except Exception as e:
                raise RuntimeError(f"Error opening image: {e}")
        
        # 构建 OpenAI 格式消息
        messages = []
        
        # 添加历史对话（如果有）
        if len(history_responses) > 0:
            for i, response in enumerate(history_responses[-self.history_n:]):
                # 用户消息（历史图片）
                if i < len(images) - 1:
                    img = images[i]
                    img_base64 = pil_to_base64(img)
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{img_base64}"}
                            }
                        ]
                    })
                
                # 助手响应
                messages.append({
                    "role": "assistant",
                    "content": response
                })
        
        # 添加当前消息（系统 prompt + 当前截图）
        if len(images) > 0:
            cur_image = images[-1]
            encoded_string = pil_to_base64(cur_image)
            
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded_string}"}
                    },
                    {
                        "type": "text",
                        "text": user_prompt
                    }
                ]
            })
        else:
            # 没有图片时只发送文本
            messages.append({
                "role": "user",
                "content": user_prompt
            })
        
        # 返回 messages 和压缩后的图片（用于获取实际发送给 API 的尺寸）
        return messages, images[-1] if images else None
    
    def call_model(
        self,
        messages: List[Dict],
        vlm_client: OpenAI,
        model_name: str,
        temperature: float = 0.0,
        **kwargs
    ) -> str:
        """
        调用 Claude 模型 API（通过 OpenAI 兼容格式）
        
        Args:
            messages: 消息列表 (可能是元组 (messages, last_image))
            vlm_client: OpenAI 客户端
            model_name: 模型名称（如 "claude-sonnet-4-20250514"）
            temperature: 温度参数
            **kwargs: 其他参数
        
        Returns:
            模型响应文本
        """
        # 如果 messages 是元组,提取 messages
        if isinstance(messages, tuple):
            messages_list = messages[0]
        else:
            messages_list = messages
        
        # 调用 OpenAI 兼容 API
        response = vlm_client.chat.completions.create(
            model=model_name,
            messages=messages_list,
            temperature=temperature,
            max_tokens=kwargs.get('max_tokens', 4096),
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
        last_image: Optional[Image.Image] = None
    ) -> Optional[List[Dict]]:
        """
        解析 Claude 模型响应
        
        Args:
            response_text: 模型响应文本
            image_width: 截图宽度
            image_height: 截图高度
            last_image: 最后一张图片(用于获取实际尺寸)
        
        Returns:
            解析后的动作列表
        """
        if last_image:
            origin_resized_height = last_image.height
            origin_resized_width = last_image.width
        else:
            origin_resized_height = image_height
            origin_resized_width = image_width
        
        try:
            parsed_responses = parse_action_to_structure_output(
                response_text,
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
            return None
    
    def response_to_code(
        self,
        parsed_responses: List[Dict],
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Image.Image] = None
    ) -> str:
        """
        将解析后的响应转换为 pyautogui 代码
        
        Args:
            parsed_responses: 解析后的动作列表
            image_width: 截图宽度
            image_height: 截图高度
        
        Returns:
            pyautogui 代码字符串
        """
        pyautogui_code = ""
        
        for parsed_response in parsed_responses:
            if "action_type" in parsed_response:
                action_type = parsed_response["action_type"]
                if action_type == FINISH_WORD:
                    return "DONE"
                elif action_type == WAIT_WORD:
                    return "WAIT"
            
            # 转换为 pyautogui 代码
            parsed_pyautogui_code = parsing_response_to_pyautogui_code(
                responses=parsed_response,
                image_height=image_height,
                image_width=image_width
            )
            pyautogui_code += parsed_pyautogui_code + "\n"
        
        return pyautogui_code.strip()
