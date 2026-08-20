"""
Base Model Adapter - 抽象基类
定义所有模型适配器必须实现的接口
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from openai import OpenAI


class BaseModelAdapter(ABC):
    """模型适配器抽象基类"""
    
    def __init__(self, runtime_conf: dict):
        """
        初始化适配器
        
        Args:
            runtime_conf: 运行时配置字典
        """
        self.runtime_conf = runtime_conf
        self.language = runtime_conf.get("language", "English")
        self.history_n = runtime_conf.get("history_n", 5)
        self.max_pixels = runtime_conf.get("max_pixels", 16384 * 28 * 28)
        self.min_pixels = runtime_conf.get("min_pixels", 100 * 28 * 28)
    
    @abstractmethod
    def build_messages(
        self,
        instruction: str,
        history_images: List[bytes],
        history_responses: List[str],
        current_screenshot: bytes
    ) -> List[Dict]:
        """
        构建模型输入消息
        
        Args:
            instruction: 用户指令
            history_images: 历史截图列表(字节数据)
            history_responses: 历史响应列表
            current_screenshot: 当前截图(字节数据)
        
        Returns:
            消息列表,符合 OpenAI API 格式
        """
        pass
    
    @abstractmethod
    def call_model(
        self,
        messages: List[Dict],
        vlm_client: OpenAI,
        model_name: str,
        temperature: float = 0.0,
        **kwargs
    ) -> str:
        """
        调用模型 API
        
        Args:
            messages: 消息列表
            vlm_client: OpenAI 客户端
            model_name: 模型名称
            temperature: 温度参数
            **kwargs: 其他参数
        
        Returns:
            模型响应文本
        """
        pass
    
    @abstractmethod
    def parse_response(
        self,
        response_text: str,
        image_width: int,
        image_height: int,
        image_info: Optional[Dict] = None,
        last_image: Optional[Any] = None
    ) -> Optional[List[Dict]]:
        """
        解析模型响应
        
        Args:
            response_text: 模型响应文本
            image_width: 截图宽度
            image_height: 截图高度
            image_info: 图像信息字典(可选,用于 Qwen)
            last_image: 最后一张图片对象(可选,用于 Doubao)
        
        Returns:
            解析后的动作列表,如果解析失败返回 None
        """
        pass
    
    @abstractmethod
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
            image_info: 图像信息字典(可选,用于 Qwen)
            last_image: 最后一张图片对象(可选,用于 Doubao)
        
        Returns:
            pyautogui 代码字符串,或特殊标记("DONE", "WAIT")
        """
        pass
