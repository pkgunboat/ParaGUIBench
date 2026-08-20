# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0
"""
XML Action Parser for Doubao Seed Agent
从官方OSWorld Doubao实现中提取的XML解析函数
"""
import re
from typing import List, Dict, Any


def parse_xml_action_v3(prediction: str, tool_schemas: List[Dict]) -> List[Dict[str, Any]]:
    """
    解析Doubao Seed模型输出的XML格式action
    
    Args:
        prediction: 模型输出的完整响应文本
        tool_schemas: 可用工具的schema定义列表
        
    Returns:
        解析后的actions列表，每个action包含function名称和parameters字典
    """
    # 提取所有function调用
    # 匹配格式: <function_never_used_51bce0c785ca2f68081bfa7d91973934=function_name>...</function_never_used_51bce0c785ca2f68081bfa7d91973934>
    function_pattern = r'<function_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</function_never_used_51bce0c785ca2f68081bfa7d91973934>'
    function_matches = re.findall(function_pattern, prediction, re.DOTALL)
    
    if not function_matches:
        return []
    
    parsed_actions = []
    
    for func_name, func_content in function_matches:
        # 提取参数
        # 匹配格式: <parameter_never_used_51bce0c785ca2f68081bfa7d91973934=param_name>value</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>
        param_pattern = r'<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>'
        param_matches = re.findall(param_pattern, func_content, re.DOTALL)
        
        parameters = {}
        for param_name, param_value in param_matches:
            # 清理参数值（去除首尾空白）
            parameters[param_name] = param_value.strip()
        
        parsed_actions.append({
            'function': func_name,
            'parameters': parameters
        })
    
    return parsed_actions


def parse_xml_action(prediction: str) -> Dict[str, Any]:
    """
    简化版XML解析，兼容旧接口
    只返回第一个解析到的action
    """
    actions = parse_xml_action_v3(prediction, [])
    if actions:
        return actions[0]
    return {}
