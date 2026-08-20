"""
Model Adapters - 支持多种模型的适配器模块
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Any

def create_adapter(model_type: str, runtime_conf: dict, use_qwen_vl: bool = False):
    """
    工厂函数:根据 model_type 创建对应的适配器
    
    Args:
        model_type: 模型类型 ("qwen", "gpt", "doubao", "ui-tars", "claude")
        runtime_conf: 运行时配置字典
        use_qwen_vl: 是否使用 Qwen VL 专用适配器（使用1000x1000坐标系统）
    
    Returns:
        BaseModelAdapter 的子类实例
    """
    if model_type == "qwen":
        if use_qwen_vl:
            from .qwen_vl_adapter import QwenVLAdapter
            return QwenVLAdapter(runtime_conf)
        else:
            from .qwen_adapter import QwenAdapter
            return QwenAdapter(runtime_conf)
    elif model_type == "gpt":
        from .gpt_adapter import GPTAdapter
        return GPTAdapter(runtime_conf)
    elif model_type in ["doubao", "ui-tars"]:
        from .doubao_adapter import DoubaoAdapter
        return DoubaoAdapter(runtime_conf)
    elif model_type == "claude":
        from .claude_adapter import ClaudeAdapter
        return ClaudeAdapter(runtime_conf)
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")

__all__ = ['create_adapter']
