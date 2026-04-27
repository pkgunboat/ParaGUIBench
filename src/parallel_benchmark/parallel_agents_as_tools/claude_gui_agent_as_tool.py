"""
Claude GUI Agent as MCP Tool
将 ClaudeComputerUseAgent 封装为可被 Plan Agent 调用的工具
使用 Claude 官方的 Computer Use API 和提示词
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
import time
from .base_agent_tool import BaseAgentTool
from parallel_agents.claude_computer_use_agent import ClaudeComputerUseAgent
from config.api_config import get_api_config, get_model_name


class ClaudeGUIAgentTool(BaseAgentTool):
    """Claude Computer Use Agent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 15, timeout: int = 0) -> Dict:
        """
        执行基于 GUI 的任务（使用 Claude Computer Use）
        
        参考 claude_computer_use_agent.py 中的 run() 方法实现
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 30 轮）
            timeout: 超时时间(秒，默认 600 秒 = 10 分钟)
        
        Returns:
            执行结果字典
        """
        start_time = time.time()
        
        # 1. 从 controller 获取 VM 信息
        vm_ip = self.controller.vm_ip
        # 从 http_server 提取端口 (格式: "http://<HOST_IP>:5001")
        http_server = self.controller.http_server
        vm_port = int(http_server.split(":")[-1])
        model_name = (
            os.environ.get("ABLATION_CLAUDE_GUI_MODEL", "").strip()
            or get_model_name("claude_gui_agent")
            or "claude-sonnet-4-5-20250929"
        )

        deerapi_config = get_api_config("deerapi")
        api_key = deerapi_config.get("api_key", "")
        base_url = deerapi_config.get("base_url", "https://api.deerapi.com/v1/")
        agent = None

        def _result(**kwargs) -> Dict:
            result_dict = self.format_result(**kwargs)
            result_dict["model_name"] = model_name
            return result_dict

        if not api_key:
            return _result(
                success=False,
                result="",
                steps=[],
                error="Missing DeerAPI/OpenAI API key. Set DEERAPI_API_KEY or OPENAI_API_KEY."
            )
        
        # 2. 创建 ClaudeComputerUseAgent 实例
        try:
            agent = ClaudeComputerUseAgent(
                vm_ip=vm_ip,
                vm_port=vm_port,
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_trajectory_length=max_rounds,
                screenshot_compression=True,
                max_screenshot_size=1280
            )
        except Exception as e:
            return _result(
                success=False,
                result="",
                steps=[],
                error=f"Failed to initialize ClaudeComputerUseAgent: {str(e)}"
            )
        
        # 创建反思总结用的 OpenAI 兼容客户端（复用 DeerAPI 配置）
        from openai import OpenAI as _OpenAI
        _reflection_client = _OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        _reflection_model = model_name

        # 3. 执行任务循环（参考 ClaudeComputerUseAgent.run() 方法）
        steps = []
        rounds_timing = []  # 记录每轮的详细时间
        thoughts = []  # 记录所有思考过程，用于反思总结
        instruction = task
        
        try:
            for round_num in range(max_rounds):
                round_start = time.time()  # 记录本轮开始时间
                # 检查超时（timeout=0 表示不限制）
                if timeout > 0 and time.time() - start_time > timeout:
                    return _result(
                        success=False,
                        result=f"Task timeout after {timeout} seconds",
                        steps=steps,
                        error="Timeout",
                        rounds_timing=rounds_timing,
                        gui_token_usage=agent.token_usage
                    )
                
                # 获取截图（使用 agent 的方法，会自动处理压缩和编码）
                screenshot_start = time.time()  # 记录截图开始时间
                try:
                    screenshot_base64 = agent.take_screenshot()
                except Exception as e:
                    return _result(
                        success=False,
                        result=f"Failed to get screenshot at round {round_num}",
                        steps=steps,
                        error=str(e),
                        rounds_timing=rounds_timing,
                        gui_token_usage=agent.token_usage
                    )
                screenshot_end = time.time()  # 记录截图结束时间
                screenshot_time = screenshot_end - screenshot_start
                
                # 构建观察
                observation = {"screenshot": screenshot_base64}
                
                # 调用 agent.predict() - 它会自动执行动作并返回结果
                # 将截图时间传递给predict，让它计入preparation_time
                think_start = time.time()
                try:
                    thought, actions, code = agent.predict(instruction, observation, screenshot_time=screenshot_time)
                    think_end = time.time()
                    thoughts.append(thought if thought else "")

                    # 输出当前轮次的 thought
                    print(f"\n{'='*60}")
                    print(f"[Claude GUI Agent] Round {round_num + 1}")
                    print(f"{'='*60}")
                    print(f"[Thought]: {thought}")
                    print(f"[Actions]: {actions}")
                    print(f"{'='*60}\n")
                    
                except Exception as e:
                    return _result(
                        success=False,
                        result=f"Error in round {round_num + 1}",
                        steps=steps,
                        error=f"Agent prediction error: {str(e)}",
                        rounds_timing=rounds_timing,
                        gui_token_usage=agent.token_usage
                    )
                
                # 动作执行开始（predict已经执行了action，这里记录执行时间）
                action_start = think_end
                action_end = time.time()
                
                # 记录轮次时间信息（包含thought和code供execution_recorder使用）
                # 从agent.round_details获取messages、screenshot_url和timing信息
                round_detail = agent.round_details[round_num] if round_num < len(agent.round_details) else {}
                timing_info = round_detail.get("timing", {})
                
                rounds_timing.append({
                    "round": round_num + 1,
                    "think_start": think_start,
                    "think_end": think_end,
                    "think_duration": think_end - think_start,
                    "action_start": action_start,
                    "action_end": action_end,
                    "action_duration": action_end - action_start,
                    "total_duration": action_end - round_start,
                    "thought": thought if thought else "",
                    "code": code if code else "",
                    "response_text": thought if thought else "",
                    "action_details": [code] if code else [],
                    "status": "running",
                    "messages": round_detail.get("messages", []),
                    "screenshot_url": round_detail.get("screenshot_url", ""),
                    "timing": timing_info
                })
                
                # 记录步骤（也添加screenshot_url）
                round_detail = agent.round_details[round_num] if round_num < len(agent.round_details) else {}
                
                step_detail = {
                    "round": round_num + 1,
                    "timestamp": time.time(),
                    "thought": thought if thought else "",  # 完整保留
                    "actions": actions if isinstance(actions, list) else [str(actions)],
                    "code": code if code else "",  # 完整保留
                    "status": "pending",
                    "output": thought[:300] if thought else "",
                    "screenshot_path": round_detail.get("screenshot_url", "")  # 添加screenshot_path供plan_agent使用
                }
                steps.append(step_detail)
                
                # 处理特殊状态
                if "DONE" in actions or code == "DONE":
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "success"

                    # 调用基类反思总结
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        client=_reflection_client, model_name=_reflection_model,
                    )
                    return _result(
                        success=True,
                        result=reflection,
                        steps=steps,
                        rounds_timing=rounds_timing,
                        gui_token_usage=agent.token_usage
                    )
                
                if "WAIT" in actions or code == "WAIT":
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "waiting"
                    time.sleep(2)
                    continue
                
                if "FAIL" in actions or code == "FAIL":
                    if steps and isinstance(steps[-1], dict):
                        steps[-1]["status"] = "failed"
                    return _result(
                        success=False,
                        result=f"Task failed at round {round_num + 1}: {thought}",
                        steps=steps,
                        error="Agent returned FAIL",
                        rounds_timing=rounds_timing,
                        gui_token_usage=agent.token_usage
                    )
                
                # 标记执行成功
                if steps and isinstance(steps[-1], dict):
                    steps[-1]["status"] = "executed"
                
                # 短暂等待
                time.sleep(2)
            
            # 达到最大轮次 - 调用基类反思总结
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                client=_reflection_client, model_name=_reflection_model,
            )
            return _result(
                success=False,
                result=reflection,
                steps=steps,
                error=f"Reached maximum rounds ({max_rounds}) without completing the task.",
                rounds_timing=rounds_timing,
                gui_token_usage=agent.token_usage
            )
            
        except Exception as e:
            return _result(
                success=False,
                result="Unexpected error during execution",
                steps=steps,
                error=str(e),
                rounds_timing=rounds_timing,
                gui_token_usage=agent.token_usage if agent else None
            )
