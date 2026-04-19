"""
ToolAgent as MCP Tool
将 ToolAgent 封装为可被 Plan Agent 调用的工具
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
import time
import json
from .base_agent_tool import BaseAgentTool
from parallel_agents.tool_agent import ToolAgent, FINISH_WORD, WAIT_WORD, FAIL_WORD


class ToolAgentTool(BaseAgentTool):
    """ToolAgent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 20, timeout: int = 600) -> Dict:
        """
        执行基于工具调用的任务
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次 (ToolAgent 可能需要更多轮次)
            timeout: 超时时间(秒)
        
        Returns:
            执行结果字典
        """
        start_time = time.time()
        
        # 1. 创建 ToolAgent 实例
        try:
            agent = ToolAgent(
                platform="ubuntu",
                action_space="tools",
                observation_type="tool_result",
                max_trajectory_length=20,
                model_name="gpt-5-2025-08-07",
                runtime_conf={
                    "language": "English",
                    "history_n": 15,
                    "temperature": 0.0,
                    "top_p": 0.9,
                    "max_tokens": 4096,
                }
            )
        except Exception as e:
            return self.format_result(
                success=False,
                result="",
                steps=[],
                error=f"Failed to initialize ToolAgent: {str(e)}"
            )
        
        # 2. 执行任务循环
        steps = []
        
        for round_num in range(max_rounds):
            # 检查超时
            if time.time() - start_time > timeout:
                return self.format_result(
                    success=False,
                    result=f"Task timeout after {timeout} seconds",
                    steps=steps,
                    error="Timeout"
                )
            
            # 调用 agent.predict()
            try:
                if round_num == 0:
                    prediction, tool_calls, status = agent.predict(task)
                else:
                    prediction, tool_calls, status = agent.predict(task, obs={})
            except Exception as e:
                return self.format_result(
                    success=False,
                    result=f"Error in round {round_num}",
                    steps=steps,
                    error=f"Agent prediction error: {str(e)}"
                )
            
            # 记录思考
            if prediction:
                steps.append(f"Round {round_num}: {prediction[:100]}...")
            
            # 处理不同状态
            if status == "completed":
                return self.format_result(
                    success=True,
                    result=f"Task completed successfully in {round_num + 1} rounds",
                    steps=steps
                )
            
            elif status == "wait":
                time.sleep(3)
                continue
            
            elif status == "fail":
                return self.format_result(
                    success=False,
                    result=f"Task failed at round {round_num}",
                    steps=steps,
                    error="Agent returned FAIL"
                )
            
            elif status == "tool_call":
                # 执行工具调用
                for tool_call in tool_calls:
                    tool_name = tool_call["name"]
                    arguments = tool_call["arguments"]
                    tool_id = tool_call["id"]
                    
                    steps.append(f"  → Calling tool: {tool_name}")
                    
                    # 执行工具
                    try:
                        result = self._execute_tool(tool_name, arguments)
                        agent.add_tool_result(tool_id, result)
                    except Exception as e:
                        error_result = {
                            "status": "error",
                            "output": f"Tool execution error: {str(e)}"
                        }
                        agent.add_tool_result(tool_id, error_result)
            
            elif status == "no_tool_call":
                # 没有工具调用,可能是思考阶段
                pass
            
            # 短暂等待
            time.sleep(1)
        
        # 超过最大轮次
        return self.format_result(
            success=False,
            result=f"Task did not complete within {max_rounds} rounds",
            steps=steps,
            error="Max rounds exceeded"
        )
    
    def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        执行工具调用 (从 run_tool_agent.py 移植)
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
        
        Returns:
            执行结果字典
        """
        # 工具执行逻辑
        if tool_name == "list_local_tools":
            code = """
import os
tools_dir = '/home/user/Desktop/tools'
if os.path.exists(tools_dir):
    files = [f for f in os.listdir(tools_dir) if f.endswith('.py')]
    print('Available tools:', ', '.join(files))
else:
    print('Tools directory not found')
"""
        
        elif tool_name == "execute_local_tool":
            script_name = arguments.get("tool_name", "")
            args = arguments.get("arguments", [])
            args_str = ", ".join([f"'{arg}'" for arg in args])
            
            code = f"""
import subprocess
tool_path = '/home/user/Desktop/tools/{script_name}'
args = [{args_str}]

try:
    result = subprocess.run(
        ['python', tool_path] + args,
        capture_output=True,
        text=True,
        timeout=30
    )
    print('=== Tool Output ===')
    print(result.stdout)
    if result.stderr:
        print('=== Tool Errors ===')
        print(result.stderr)
except Exception as e:
    print(f'Error executing tool: {{e}}')
"""
        
        elif tool_name == "read_file":
            file_path = arguments.get("file_path", "")
            code = f"""
import os
try:
    if os.path.exists('{file_path}'):
        with open('{file_path}', 'r', encoding='utf-8') as f:
            content = f.read()
        print('=== File Content ===')
        print(content)
    else:
        print('Error: File not found')
except Exception as e:
    print(f'Error reading file: {{e}}')
"""
        
        elif tool_name == "list_directory":
            directory_path = arguments.get("directory_path", "")
            code = f"""
import os
try:
    if os.path.exists('{directory_path}'):
        items = os.listdir('{directory_path}')
        print('=== Directory Contents ===')
        for item in items:
            item_path = os.path.join('{directory_path}', item)
            if os.path.isdir(item_path):
                print(f'[DIR]  {{item}}')
            else:
                print(f'[FILE] {{item}}')
    else:
        print('Error: Directory not found')
except Exception as e:
    print(f'Error listing directory: {{e}}')
"""
        
        elif tool_name == "check_file_exists":
            path = arguments.get("path", "")
            code = f"""
import os
path = '{path}'
exists = os.path.exists(path)
print(f'Path: {{path}}')
print(f'Exists: {{exists}}')
if exists:
    is_file = os.path.isfile(path)
    is_dir = os.path.isdir(path)
    print(f'Type: {{"File" if is_file else "Directory" if is_dir else "Other"}}')
"""
        
        elif tool_name == "calculator":
            expression = arguments.get("expression", "")
            code = f"""
import ast
import operator

def calculator(expression):
    # Safe operators
    operators = {{
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }}
    
    def eval_expr(node):
        if isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.BinOp):
            return operators[type(node.op)](eval_expr(node.left), eval_expr(node.right))
        elif isinstance(node, ast.UnaryOp):
            return operators[type(node.op)](eval_expr(node.operand))
        else:
            raise TypeError(f"Unsupported type: {{type(node)}}")
    
    try:
        tree = ast.parse(expression, mode='eval')
        result = eval_expr(tree.body)
        return result
    except Exception as e:
        return f"Error: {{e}}"

result = calculator('{expression}')
print(f'Expression: {expression}')
print(f'Result: {{result}}')
"""
        
        elif tool_name == "search_files":
            keyword = arguments.get("keyword", "")
            root_dir = arguments.get("root_dir", "~/Desktop")
            code = f"""
import os
import fnmatch

def search_files(keyword, root_dir='~/Desktop'):
    root_dir = os.path.expanduser(root_dir)
    matches = []
    
    try:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            for filename in filenames:
                if keyword.lower() in filename.lower():
                    full_path = os.path.join(dirpath, filename)
                    matches.append(full_path)
    except Exception as e:
        return f"Error: {{e}}"
    
    return matches

keyword = '{keyword}'
root_dir = '{root_dir}'
results = search_files(keyword, root_dir)

print(f'Searching for "{{keyword}}" in {{root_dir}}')
print(f'Found {{len(results)}} file(s):')
for path in results[:20]:  # Limit to 20 results
    print(f'  {{path}}')
if len(results) > 20:
    print(f'  ... and {{len(results) - 20}} more')
"""
        
        elif tool_name == "get_volume":
            code = """
import subprocess

def get_volume():
    try:
        result = subprocess.run(
            ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout
        volume_str = output.split('/')[1].strip()
        volume = int(volume_str.replace('%', ''))
        return volume
    except Exception as e:
        return f"Error: {e}"

volume = get_volume()
print(f'Current system volume: {volume}%')
"""
        
        elif tool_name == "set_volume":
            percent = arguments.get("percent", 50)
            code = f"""
import subprocess

def set_volume(percent):
    try:
        percent = max(0, min(100, int(percent)))
        subprocess.run(
            ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{{percent}}%'],
            check=True
        )
        return f"Volume set to {{percent}}%"
    except Exception as e:
        return f"Error: {{e}}"

result = set_volume({percent})
print(result)
"""
        
        elif tool_name == "git_set_user":
            username = arguments.get("username", "")
            email = arguments.get("email", "")
            code = f"""
import subprocess

def git_set_user_info(username, email):
    try:
        subprocess.run(['git', 'config', '--global', 'user.name', username], check=True)
        subprocess.run(['git', 'config', '--global', 'user.email', email], check=True)
        return f"Git user set to: {{username}} <{{email}}>"
    except Exception as e:
        return f"Error: {{e}}"

username = '{username}'
email = '{email}'
result = git_set_user_info(username, email)
print(result)
"""
        
        elif tool_name == "chrome_restore_tab":
            code = """
import pyautogui
import time

def chrome_restore_tab():
    try:
        # 恢复上次关闭的标签页 (Ctrl+Shift+T)
        pyautogui.hotkey('ctrl', 'shift', 't')
        time.sleep(0.5)
        return "Restored last closed Chrome tab"
    except Exception as e:
        return f"Error: {{e}}"

result = chrome_restore_tab()
print(result)
"""
        
        elif tool_name == "chrome_print_page":
            code = """
import pyautogui
import time

def chrome_print_page():
    try:
        # 打开打印对话框 (Ctrl+P)
        pyautogui.hotkey('ctrl', 'p')
        time.sleep(0.5)
        return "Opened Chrome print dialog"
    except Exception as e:
        return f"Error: {{e}}"

result = chrome_print_page()
print(result)
"""
        
        elif tool_name == "chrome_bookmark_page":
            code = """
import pyautogui
import time

def chrome_bookmark_page():
    try:
        # 收藏当前页面 (Ctrl+D)
        pyautogui.hotkey('ctrl', 'd')
        time.sleep(0.5)
        return "Bookmarked current Chrome page"
    except Exception as e:
        return f"Error: {{e}}"

result = chrome_bookmark_page()
print(result)
"""
        
        elif tool_name == "chrome_clear_data":
            code = """
import pyautogui
import time

def chrome_clear_data():
    try:
        # 打开清除浏览数据窗口 (Ctrl+Shift+Del)
        pyautogui.hotkey('ctrl', 'shift', 'del')
        time.sleep(0.5)
        return "Opened Chrome clear browsing data dialog"
    except Exception as e:
        return f"Error: {{e}}"

result = chrome_clear_data()
print(result)
"""
        
        else:
            return {"status": "error", "output": f"Unknown tool: {tool_name}"}
        
        # 执行代码
        result = self.controller.execute_python_command(code)
        
        if result and result.get("status") == "success":
            return {"status": "success", "output": result.get("output", "")}
        else:
            return {"status": "error", "output": result.get("output", "Execution failed") if result else "No result"}
