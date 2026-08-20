"""
PyAutoGUI Code Parser for Doubao Seed Agent
Parses Python pyautogui code from model predictions
"""
import re
from typing import List, Dict, Any, Optional


def parse_pyautogui_code(prediction: str) -> List[Dict[str, Any]]:
    """
    解析 Doubao Seed 模型输出的 pyautogui Python 代码
    
    Args:
        prediction: 模型输出的完整响应文本
        
    Returns:
        解析后的 actions 列表，每个 action 包含 function 名称和 parameters 字典
    """
    # 提取代码块中的内容
    code_pattern = r'```python\s*\n(.*?)\n```'
    code_matches = re.findall(code_pattern, prediction, re.DOTALL)
    
    if not code_matches:
        # 也可能是特殊代码 DONE/WAIT/FAIL
        prediction_upper = prediction.strip().upper()
        if 'DONE' in prediction_upper:
            return [{'function': 'finished', 'parameters': {}}]
        elif 'WAIT' in prediction_upper:
            return [{'function': 'wait', 'parameters': {'time': 5}}]
        elif 'FAIL' in prediction_upper:
            return [{'function': 'finished', 'parameters': {}}]  # 视为任务结束
        return []
    
    code = code_matches[0].strip()
    
    # 移除注释行
    lines = code.split('\n')
    code_line = None
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            code_line = line
            break
    
    if not code_line:
        return []
    
    # 解析不同的 pyautogui 命令
    actions = []
    
    # 1. pyautogui.click(x=100, y=200) or pyautogui.click(100, 200)
    click_pattern = r'pyautogui\.click\s*\(\s*(?:x\s*=\s*)?(\d+)\s*,\s*(?:y\s*=\s*)?(\d+)'
    match = re.search(click_pattern, code_line)
    if match:
        x, y = match.groups()
        actions.append({
            'function': 'click',
            'parameters': {'point': f"{x} {y}"}
        })
        return actions
    
    # 2. pyautogui.doubleClick(x=100, y=200)
    double_click_pattern = r'pyautogui\.doubleClick\s*\(\s*(?:x\s*=\s*)?(\d+)\s*,\s*(?:y\s*=\s*)?(\d+)'
    match = re.search(double_click_pattern, code_line)
    if match:
        x, y = match.groups()
        actions.append({
            'function': 'left_double',
            'parameters': {'point': f"{x} {y}"}
        })
        return actions
    
    # 3. pyautogui.rightClick(x=100, y=200)
    right_click_pattern = r'pyautogui\.rightClick\s*\(\s*(?:x\s*=\s*)?(\d+)\s*,\s*(?:y\s*=\s*)?(\d+)'
    match = re.search(right_click_pattern, code_line)
    if match:
        x, y = match.groups()
        actions.append({
            'function': 'right_single',
            'parameters': {'point': f"{x} {y}"}
        })
        return actions
    
    # 4. pyautogui.typewrite('text') or pyautogui.write('text')
    type_pattern = r'pyautogui\.(?:typewrite|write)\s*\(\s*["\']([^"\']*)["\']'
    match = re.search(type_pattern, code_line)
    if match:
        text = match.group(1)
        actions.append({
            'function': 'type',
            'parameters': {'content': text}
        })
        return actions
    
    # 5. pyautogui.hotkey('ctrl', 'c') or pyautogui.hotkey('ctrl', 'shift', 's')
    hotkey_pattern = r'pyautogui\.hotkey\s*\((.*?)\)'
    match = re.search(hotkey_pattern, code_line)
    if match:
        keys_str = match.group(1)
        # 提取所有引号中的键
        keys = re.findall(r'["\']([^"\']+)["\']', keys_str)
        if keys:
            actions.append({
                'function': 'hotkey',
                'parameters': {'key': ' '.join(keys)}
            })
            return actions
    
    # 6. pyautogui.press('enter') or pyautogui.press('Return')
    press_pattern = r'pyautogui\.press\s*\(\s*["\']([^"\']*)["\']'
    match = re.search(press_pattern, code_line)
    if match:
        key = match.group(1)
        actions.append({
            'function': 'hotkey',
            'parameters': {'key': key.lower()}
        })
        return actions
    
    # 7. pyautogui.scroll(clicks, x, y) or pyautogui.scroll(clicks)
    scroll_pattern = r'pyautogui\.scroll\s*\(\s*(-?\d+)(?:\s*,\s*(\d+)\s*,\s*(\d+))?'
    match = re.search(scroll_pattern, code_line)
    if match:
        clicks = int(match.group(1))
        x = match.group(2) if match.group(2) else '960'  # 默认屏幕中心
        y = match.group(3) if match.group(3) else '540'
        direction = 'up' if clicks > 0 else 'down'
        actions.append({
            'function': 'scroll',
            'parameters': {
                'point': f"{x} {y}",
                'direction': direction
            }
        })
        return actions
    
    # 8. pyautogui.drag(x, y) or pyautogui.dragTo(x, y)
    drag_pattern = r'pyautogui\.drag(?:To)?\s*\(\s*(?:x\s*=\s*)?(\d+)\s*,\s*(?:y\s*=\s*)?(\d+)'
    match = re.search(drag_pattern, code_line)
    if match:
        end_x, end_y = match.groups()
        # 拖拽需要起点，默认使用当前位置的近似值
        actions.append({
            'function': 'drag',
            'parameters': {
                'start_point': '0 0',  # 这里需要根据上下文确定起点
                'end_point': f"{end_x} {end_y}"
            }
        })
        return actions
    
    return actions


def extract_thought(prediction: str) -> str:
    """
    从 prediction 中提取思考过程
    
    Args:
        prediction: 模型输出
        
    Returns:
        思考内容
    """
    # 查找 Observation: 和 Thought: 部分
    thought_pattern = r'Thought:\s*(.*?)(?=```|$)'
    match = re.search(thought_pattern, prediction, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # 如果没有明确的 Thought 标记，尝试提取代码块之前的内容
    code_pattern = r'```python'
    parts = re.split(code_pattern, prediction, maxsplit=1)
    if len(parts) > 1:
        return parts[0].strip()
    
    return prediction.strip()
