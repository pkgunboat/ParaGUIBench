"""
Qwen Adapter - Qwen 模型适配器
集成 Qwen3-VL 的 function calling 调用方式
"""
import sys
import os
import base64
from io import BytesIO
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image
from openai import OpenAI

# 添加路径以导入工具
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from model_adapters.base_adapter import BaseModelAdapter
except ImportError:
    # 如果在包内导入失败,尝试相对导入
    from .base_adapter import BaseModelAdapter
from utils.qwen_action_parser import (
    parse_qwen_response,
    qwen_action_to_pyautogui
)
from utils.action_parser import smart_resize

try:
    from qwen_agent.llm.fncall_prompts.nous_fncall_prompt import (
        NousFnCallPrompt,
        Message,
        ContentItem,
    )
    from cookbooks.qwen.agent_function_call import ComputerUse
    QWEN_AGENT_AVAILABLE = True
except ImportError:
    QWEN_AGENT_AVAILABLE = False
    print("Warning: qwen_agent not available. Qwen adapter will not work.")


class QwenAdapter(BaseModelAdapter):
    """Qwen 模型适配器"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化 Qwen 适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        super().__init__(runtime_conf)
        
        if not QWEN_AGENT_AVAILABLE:
            raise ImportError("qwen_agent package is required for Qwen adapter")
        
        # Qwen 专用配置
        self.qwen_display_width = runtime_conf.get("qwen_display_width", 1000)
        self.qwen_display_height = runtime_conf.get("qwen_display_height", 1000)
        
        # 初始化 ComputerUse 工具
        self.computer_use = ComputerUse(
            cfg={
                "display_width_px": self.qwen_display_width,
                "display_height_px": self.qwen_display_height
            }
        )
    
    def build_messages(
        self,
        instruction: str,
        history_images: List[bytes],
        history_responses: List[str],
        current_screenshot: bytes
    ) -> List[Dict]:
        """
        构建 Qwen 模型的消息格式(使用 function calling)
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表
            history_responses: 历史响应列表
            current_screenshot: 当前截图
        
        Returns:
            消息列表(元组,包含 messages 和 last_image)
        """
        # 构建系统消息(包含工具定义)
        system_message = NousFnCallPrompt().preprocess_fncall_messages(
            messages=[
                Message(role="system", content=[ContentItem(text="You are a helpful assistant.")]),
            ],
            functions=[self.computer_use.function],
            lang=None,
        )
        system_message = system_message[0].model_dump()
        
        # 初始化消息列表
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": msg["text"]} for msg in system_message["content"]
                ],
            }
        ]
        
        # 计算当前截图尺寸用于坐标转换
        current_image = Image.open(BytesIO(current_screenshot))
        original_width, original_height = current_image.size
        
        # 使用 smart_resize 计算 Qwen 使用的缩放尺寸
        resized_height, resized_width = smart_resize(
            original_height,
            original_width,
            factor=32,
            min_pixels=self.min_pixels,
            max_pixels=self.max_pixels,
        )
        
        # 添加历史对话(最多 history_n 轮)
        history_messages = []
        if history_responses:
            # 获取最近的历史
            recent_history = history_responses[-(self.history_n * 2):] if len(history_responses) > self.history_n * 2 else history_responses
            
            # 构建历史消息(不包含图片,避免消息过大)
            for idx, response in enumerate(recent_history[::2]):  # 每两个响应为一轮对话
                history_messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction if idx == 0 else "Continue the task."}
                    ]
                })
                # 找到对应的 assistant 响应
                resp_idx = idx * 2
                if resp_idx < len(recent_history):
                    history_messages.append({
                        "role": "assistant",
                        "content": recent_history[resp_idx]
                    })
        
        messages.extend(history_messages)
        
        # 添加当前轮的用户消息(包含截图)
        base64_image = base64.b64encode(current_screenshot).decode('utf-8')
        current_user_message = {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                },
                {"type": "text", "text": instruction if not history_responses else "Continue the task."},
            ],
        }
        messages.append(current_user_message)
        
        # 返回消息列表和图像尺寸信息
        return messages, {
            "original_width": original_width,
            "original_height": original_height,
            "resized_width": resized_width,
            "resized_height": resized_height
        }
    
    def call_model(
        self,
        messages: List[Dict],
        vlm_client: OpenAI,
        model_name: str,
        temperature: float = 0.0,
        **kwargs
    ) -> str:
        """
        调用 Qwen 模型 API
        
        Args:
            messages: 消息列表(可能是元组)
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
            max_tokens=kwargs.get("max_tokens", 1000),
            temperature=temperature,
        )
        response_text = response.choices[0].message.content
        if response_text:
            return response_text.strip()
        else:
            return ""
    
    def parse_response(
        self,
        response_text: str,
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Any] = None
    ) -> Optional[List[Dict]]:
        """
        解析 Qwen 模型响应
        
        Args:
            response_text: 模型响应文本
            image_width: 截图宽度
            image_height: 截图高度
            image_info: 图像信息字典(包含原始和缩放尺寸)
        
        Returns:
            解析后的动作字典,如果解析失败返回 None
        """
        action_dict = parse_qwen_response(response_text)
        if action_dict is None:
            return None
        
        # 如果需要保存图像信息用于坐标转换,可以附加到返回结果中
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
            pyautogui 代码字符串,或特殊标记("DONE", "WAIT")
        """
        if not parsed_responses:
            return ""
        
        action_dict = parsed_responses[0]
        
        # 提取图像信息
        if image_info:
            original_width = image_info.get("original_width", image_width)
            original_height = image_info.get("original_height", image_height)
            resized_width = image_info.get("resized_width", self.qwen_display_width)
            resized_height = image_info.get("resized_height", self.qwen_display_height)
        else:
            # 如果没有提供 image_info,使用默认值
            original_width = image_width
            original_height = image_height
            resized_width = self.qwen_display_width
            resized_height = self.qwen_display_height
        
        # 检查特殊动作
        if action_dict and 'arguments' in action_dict:
            arguments = action_dict['arguments']
            action = arguments.get('action', '')
            
            if action == "wait":
                return "WAIT"
            elif action in ["terminate", "answer"]:
                return "DONE"
        
        # 转换为 pyautogui 代码
        pyautogui_code = qwen_action_to_pyautogui(
            action_dict,
            image_width=original_width,
            image_height=original_height,
            resized_width=resized_width,
            resized_height=resized_height
        )
        
        return pyautogui_code
