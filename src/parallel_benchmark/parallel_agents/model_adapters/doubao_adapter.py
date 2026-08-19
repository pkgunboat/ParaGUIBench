"""
Doubao Adapter - Doubao/UI-TARS 模型适配器
将现有的 Doubao 模型调用逻辑封装为适配器
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
    # 如果在包内导入失败,尝试相对导入
    from .base_adapter import BaseModelAdapter
from utils.prompt import COMPUTER_USE_DOUBAO, COMPUTER_USE_GUI_AGENT
from utils.action_parser import (
    parse_action_to_structure_output,
    parsing_response_to_pyautogui_code,
    add_box_token as add_box_token_func
)
from utils.gui_agent_tools import pil_to_base64

FINISH_WORD = "finished"
WAIT_WORD = "wait"


class DoubaoAdapter(BaseModelAdapter):
    """Doubao/UI-TARS 模型适配器"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化 Doubao 适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        super().__init__(runtime_conf)
        self.model_type = runtime_conf.get("model_type", "doubao")
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
        构建 Doubao 模型的消息格式
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表
            history_responses: 历史响应列表
            current_screenshot: 当前截图
        
        Returns:
            消息列表
        """
        # 选择 prompt
        if self.model_type == "doubao":
            user_prompt = COMPUTER_USE_DOUBAO.format(
                instruction=instruction,
                language=self.language
            )
        else:
            user_prompt = COMPUTER_USE_GUI_AGENT.format(
                instruction=instruction,
                language=self.language
            )
        
        # 处理图像历史
        images = []
        if isinstance(history_images, bytes):
            history_images = [history_images]
        elif isinstance(history_images, np.ndarray):
            history_images = list(history_images)
        elif isinstance(history_images, list):
            pass
        else:
            raise TypeError(f"Unidentified images type: {type(history_images)}")
        
        # 添加当前截图
        all_images = history_images + [current_screenshot]
        
        # 转换为 PIL Image 并限制数量
        for image_bytes in all_images[-self.history_n:]:
            try:
                image = Image.open(BytesIO(image_bytes))
                if image.mode != "RGB":
                    image = image.convert("RGB")
                images.append(image)
            except Exception as e:
                raise RuntimeError(f"Error opening image: {e}")
        
        # 构建消息
        messages = [
            {
                "role": "system",
                "content": user_prompt
            },
        ]
        
        # 添加历史对话
        image_num = 0
        if len(history_responses) > 0:
            for history_idx, history_response in enumerate(history_responses):
                # 只发送最近 history_n 张图片
                if history_idx + self.history_n > len(history_responses):
                    if image_num < len(images):
                        cur_image = images[image_num]
                        encoded_string = pil_to_base64(cur_image)
                        messages.append({
                            "role": "user",
                            "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_string}"}}]
                        })
                        image_num += 1
                
                messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": add_box_token_func(history_response)}]
                })
        
        # 添加当前截图
        if image_num < len(images):
            cur_image = images[image_num]
            encoded_string = pil_to_base64(cur_image)
            messages.append({
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_string}"}}]
            })
        else:
            # 如果没有历史图片,使用最后一张
            if len(images) > 0:
                cur_image = images[-1]
                encoded_string = pil_to_base64(cur_image)
                messages.append({
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_string}"}}]
                })
        
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
        调用 Doubao 模型 API
        
        Args:
            messages: 消息列表 (可能是元组 (messages, last_image))
            vlm_client: OpenAI 客户端
            model_name: 模型名称
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
        
        response = vlm_client.chat.completions.create(
            model=model_name,
            messages=messages_list,
        )
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
        解析 Doubao 模型响应
        
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
        actions = []
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
                image_width=image_width,
                input_swap=self.input_swap
            )
            actions.append(parsed_pyautogui_code)
            pyautogui_code = parsed_pyautogui_code
        
        return pyautogui_code if pyautogui_code else ""
