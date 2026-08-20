from openai import OpenAI
import time
import os
import json
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime


class VLMRecorder:
    """
    记录 VLM 调用信息的类，包括调用时间、响应内容等。
    用于追踪和分析 VLM API 的调用情况。
    """
    
    def __init__(self, api_key: str, base_url: str, model_name: str, 
                 agent_type: str, agent_id: str):
        """
        初始化 VLM 记录器
        
        Args:
            api_key: VLM API 密钥
            base_url: VLM API 基础 URL
            model_name: 模型名称
            agent_type: Agent 类型
            agent_id: Agent ID
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.agent_type = agent_type
        self.agent_id = agent_id
        self.vlm = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        self.call_history: List[Dict[str, Any]] = []

    def log(self, message: str):
        """简单的日志输出"""
        print(message)

    def predict(self, messages: List[Dict[str, Any]], **kwargs) -> Tuple[Any, Dict[str, Any]]:
        """
        调用 VLM 并记录调用信息
        
        Args:
            messages: 对话消息列表
            **kwargs: 其他传递给 VLM API 的参数
            
        Returns:
            (response, call_record): VLM 响应对象和调用记录字典
        """
        # 记录开始时间
        start_time = time.time()
        start_datetime = datetime.now().isoformat()
        
        # 调用 VLM API
        try:
            response = self.vlm.responses.create(
                model=self.model_name,
                instructions="You are a helpful assistant",
                input=messages,
                reasoning={
                    "effort": "high",
                    "summary": "auto",
                },
                **kwargs
            )
        except Exception as e:
            # 如果调用失败，记录错误信息
            end_time = time.time()
            end_datetime = datetime.now().isoformat()
            call_record = {
                "agent_type": self.agent_type,
                "agent_id": self.agent_id,
                "model_name": self.model_name,
                "start_time": start_datetime,
                "end_time": end_datetime,
                "duration_seconds": end_time - start_time,
                "thought": None,
                "content": None,
                "error": str(e),
                "success": False
            }
            self.call_history.append(call_record)
            raise
        
        # 记录结束时间
        end_time = time.time()
        end_datetime = datetime.now().isoformat()
        duration = end_time - start_time
        print(response)
        # 提取响应内容
        # if response and response.choices and len(response.choices) > 0:
        #     message_obj = response.choices[0].message
            
        #     # 提取 content
        #     content = message_obj.content if hasattr(message_obj, 'content') else None
            
        #     # 提取 reasoning_content 字段作为 thought
        #     thought = None
        #     if hasattr(message_obj, 'reasoning_content'):
        #         thought = message_obj.reasoning_content
            
        # else:
        #     thought = None
        #     content = None
        
        # 构建调用记录字典
        call_record = {
            "agent_type": self.agent_type,
            "agent_id": self.agent_id,
            "model_name": self.model_name,
            "start_time": start_datetime,
            "end_time": end_datetime,
            "duration_seconds": duration,
            "thought": None,
            "content": None,
            "success": True
        }
        
        # 保存到历史记录
        self.call_history.append(call_record)
        
        return response, call_record
    
    def get_call_history(self) -> List[Dict[str, Any]]:
        """
        获取所有调用历史记录
        
        Returns:
            调用历史记录列表
        """
        return self.call_history.copy()
    
    def save_history_to_file(self, filepath: str):
        """
        将调用历史保存到 JSON 文件
        
        Args:
            filepath: 保存的文件路径
        """
        # 确保目录存在
        dirname = os.path.dirname(filepath)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.call_history, f, indent=2, ensure_ascii=False)
    
    def clear_history(self):
        """清空调用历史"""
        self.call_history.clear()


# 为了向后兼容，保留 VLMLogger 作为别名
test_vlm_recoder = VLMRecorder(
    api_key="${OPENAI_API_KEY}", 
    base_url="https://api.deerapi.com/v1/",   
    model_name="gpt-5-2025-08-07",
    agent_type="test",
    agent_id="test"
)
response, call_record = test_vlm_recoder.predict(
    messages=[{"role": "user", "content": "please think about the answer to the question step by step: how many countries has more than 100 million people? "}]
)
print(response.choices[0].message.content)
print(response.choices[0].message)
print(call_record)
