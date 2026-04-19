"""
GUIAgent as MCP Tool
将 GUIAgent 封装为可被 Plan Agent 调用的工具
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
import time
import re
from .base_agent_tool import BaseAgentTool
from parallel_agents.gui_agent import GUIAgent, FINISH_WORD, WAIT_WORD


class GUIAgentTool(BaseAgentTool):
    """GUIAgent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 50, timeout: int = 600) -> Dict:
        """
        执行基于 GUI 的任务
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 50 轮）
            timeout: 超时时间(秒，默认 600 秒 = 10 分钟)
        
        Returns:
            执行结果字典
        """
        start_time = time.time()
        
        # 1. 创建 GUIAgent 实例
        try:
            agent = GUIAgent(
                platform="ubuntu",
                action_space="pyautogui",
                observation_type="screenshot",
                max_trajectory_length=50,
                model_type="claude",  # 使用 Claude Sonnet 4.5
                runtime_conf={
                    "input_swap": False,
                    "language": "English",
                    "history_n": 5,
                    "max_pixels": 16384*28*28,
                    "min_pixels": 100*28*28,
                    "callusr_tolerance": 3,
                    "temperature": 0.0,
                    "top_k": -1,
                    "top_p": 0.9,
                    "max_tokens": 4096,
                    # Claude API 配置（通过 OpenAI 兼容格式，如 deerapi）
                    "claude_api_key": "${OPENAI_API_KEY}",
                    "claude_base_url": "https://api.deerapi.com/v1/",
                    "claude_model_name": "claude-sonnet-4-5"
                }
            )
        except Exception as e:
            return self.format_result(
                success=False,
                result="",
                steps=[],
                error=f"Failed to initialize GUIAgent: {str(e)}"
            )
        
        # 创建反思总结用的 OpenAI 兼容客户端（复用 DeerAPI 配置）
        from openai import OpenAI as _OpenAI
        _reflection_client = _OpenAI(
            api_key="${OPENAI_API_KEY}",
            base_url="https://api.deerapi.com/v1/"
        )
        _reflection_model = "claude-sonnet-4-5"

        # 2. 执行任务循环
        steps = []
        thoughts = []  # 记录所有思考过程，用于反思总结
        # Token 消耗：GUIAgent 当前不暴露 token_usage，预留字段供未来适配
        _gui_token_usage = None

        for round_num in range(max_rounds):
            # 检查超时
            if time.time() - start_time > timeout:
                return self.format_result(
                    success=False,
                    result=f"Task timeout after {timeout} seconds",
                    steps=steps,
                    error="Timeout",
                    gui_token_usage=_gui_token_usage,
                )
            
            # 获取截图
            try:
                screenshot = self.controller.get_screenshot()
                if screenshot is None:
                    return self.format_result(
                        success=False,
                        result=f"Failed to get screenshot at round {round_num}",
                        steps=steps,
                        error="Screenshot capture failed",
                        gui_token_usage=_gui_token_usage,
                    )
            except Exception as e:
                return self.format_result(
                    success=False,
                    result=f"Error getting screenshot at round {round_num}",
                    steps=steps,
                    error=str(e),
                    gui_token_usage=_gui_token_usage,
                )
            
            # 构建观察
            observation = {"screenshot": screenshot}
            
            # 调用 agent.predict()
            try:
                response, actions, pyautogui_code = agent.predict(task, observation)
            except Exception as e:
                return self.format_result(
                    success=False,
                    result=f"Error in round {round_num}",
                    steps=steps,
                    error=f"Agent prediction error: {str(e)}",
                    gui_token_usage=_gui_token_usage,
                )

            # 记录思考过程
            thoughts.append(response if response else "")

            # 记录详细步骤（包含 thought 和 action）
            # 注意：actions 是字符串列表（pyautogui 代码），不是字典
            step_detail = {
                "round": round_num + 1,
                "timestamp": time.time(),  # 添加步骤时间戳，用于视频同步
                "thought": response if response else "",  # LLM 的思考过程（完整保留）
                "actions": [],  # 从 pyautogui_code 解析的动作描述
                "code": pyautogui_code if pyautogui_code and isinstance(pyautogui_code, str) else "",  # 生成的代码（完整保留）
                "status": "pending",
                "output": response[:300] if response else ""
            }
            
            # 从 pyautogui 代码中提取动作类型（简单解析）
            if pyautogui_code and isinstance(pyautogui_code, str):
                # 解析代码，提取操作类型
                if "pyautogui.click" in pyautogui_code:
                    # 提取坐标
                    match = re.search(r'pyautogui\.click\(([^,]+),\s*([^,)]+)', pyautogui_code)
                    if match:
                        x, y = match.groups()
                        step_detail["actions"].append(f"Click at ({x}, {y})")
                    else:
                        step_detail["actions"].append("Click")
                
                if "pyautogui.write" in pyautogui_code or "pyautogui.typewrite" in pyautogui_code:
                    # 提取输入内容
                    match = re.search(r'pyautogui\.(?:write|typewrite)\([\'"]([^\'"]{0,50})', pyautogui_code)
                    if match:
                        content = match.group(1)
                        step_detail["actions"].append(f"Type: '{content}...'")
                    else:
                        step_detail["actions"].append("Type text")
                
                if "pyautogui.scroll" in pyautogui_code:
                    # 提取滚动方向
                    if "-" in pyautogui_code:
                        step_detail["actions"].append("Scroll down")
                    else:
                        step_detail["actions"].append("Scroll up")
                
                if "pyautogui.keyDown" in pyautogui_code or "pyautogui.press" in pyautogui_code:
                    # 提取按键
                    match = re.search(r"pyautogui\.(?:keyDown|press)\(['\"]([^'\"]+)", pyautogui_code)
                    if match:
                        key = match.group(1)
                        step_detail["actions"].append(f"Press {key}")
                    else:
                        step_detail["actions"].append("Press key")
            
            steps.append(step_detail)
            
            # 处理特殊动作
            if pyautogui_code in ["DONE", "WAIT", "FAIL"] or (isinstance(pyautogui_code, str) and pyautogui_code.startswith("DONE:")):
                if pyautogui_code == "DONE" or (isinstance(pyautogui_code, str) and pyautogui_code.startswith("DONE:")):
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "success"

                    # 调用基类反思总结
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        client=_reflection_client, model_name=_reflection_model,
                    )
                    return self.format_result(
                        success=True,
                        result=reflection,
                        steps=steps,
                        gui_token_usage=_gui_token_usage,
                    )
                elif pyautogui_code == "WAIT":
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "waiting"
                    time.sleep(2)
                    continue
                elif pyautogui_code == "FAIL":
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "failed"
                    return self.format_result(
                        success=False,
                        result=f"Task failed at round {round_num}",
                        steps=steps,
                        error="Agent returned FAIL",
                        gui_token_usage=_gui_token_usage,
                    )
            
            # 检查是否完成(通过 FINISH_WORD)
            if FINISH_WORD in response:
                if steps and isinstance(steps[-1], dict):
                    steps[-1]["status"] = "success"
                # 调用基类反思总结
                reflection = self._generate_reflection_summary(
                    task, steps, thoughts, "success",
                    client=_reflection_client, model_name=_reflection_model,
                )
                return self.format_result(
                    success=True,
                    result=reflection,
                    steps=steps,
                    gui_token_usage=_gui_token_usage,
                )

            # 执行 PyAutoGUI 代码
            if pyautogui_code:
                try:
                    # 缩放坐标：Claude 基于 1280x720 返回坐标，需要缩放到实际屏幕 1920x1080
                    scaled_code = self._scale_pyautogui_coordinates(pyautogui_code, 1280, 720, 1920, 1080)
                    result = self.controller.execute_python_command(scaled_code)
                    if not result or result.get("status") != "success":
                        if steps and isinstance(steps[-1], dict):
                            steps[-1]["status"] = "warning"
                            steps[-1]["warning"] = "Action execution issue"
                    else:
                        if steps and isinstance(steps[-1], dict):
                            steps[-1]["status"] = "executed"
                except Exception as e:
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "error"
                        steps[-1]["error"] = str(e)
            
            # 短暂等待
            time.sleep(2)
        
        # 达到最大轮次 - 调用基类反思总结
        reflection = self._generate_reflection_summary(
            task, steps, thoughts, "max_rounds",
            client=_reflection_client, model_name=_reflection_model,
        )
        return self.format_result(
            success=False,
            result=reflection,
            steps=steps,
            error=f"Reached maximum rounds ({max_rounds}) without completing the task.",
            gui_token_usage=_gui_token_usage,
        )
    
    def _scale_pyautogui_coordinates(self, code: str, src_width: int, src_height: int, dst_width: int, dst_height: int) -> str:
        """
        缩放 pyautogui 代码中的坐标
        
        Args:
            code: 原始 pyautogui 代码
            src_width: 源图片宽度（模型看到的）
            src_height: 源图片高度（模型看到的）
            dst_width: 目标屏幕宽度（实际执行的）
            dst_height: 目标屏幕高度（实际执行的）
        
        Returns:
            缩放后的代码
        """
        if src_width == dst_width and src_height == dst_height:
            return code
        
        scale_x = dst_width / src_width
        scale_y = dst_height / src_height
        
        # 匹配 pyautogui 命令中的坐标
        # 格式：pyautogui.click(x, y, ...)  或  pyautogui.doubleClick(x, y, ...)  等
        import re
        
        def scale_coords(match):
            cmd = match.group(1)  # 命令名（click, doubleClick 等）
            x = float(match.group(2))
            y = float(match.group(3))
            rest = match.group(4)  # 其余参数
            
            # 缩放坐标
            new_x = round(x * scale_x, 2)
            new_y = round(y * scale_y, 2)
            
            return f"pyautogui.{cmd}({new_x}, {new_y}{rest})"
        
        # 匹配各种 pyautogui 命令
        pattern = r'pyautogui\.(click|doubleClick|moveTo|dragTo)\((\d+\.?\d*),\s*(\d+\.?\d*)(.*?)\)'
        scaled_code = re.sub(pattern, scale_coords, code)
        
        return scaled_code

