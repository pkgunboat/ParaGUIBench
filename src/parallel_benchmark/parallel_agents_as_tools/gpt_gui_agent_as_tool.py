"""
GPT GUI Agent as Tool
将 GUIAgent (GPT模式) 封装为可被 Plan Agent 调用的工具
"""
import sys
import os

from typing import Dict
import time
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config


class GPTGUIAgentTool(BaseAgentTool):
    """GPT GUI Agent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 15, timeout: int = 300) -> Dict:
        """
        执行基于 GUI 的任务（使用 GPT-5）
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 15 轮）
            timeout: 超时时间(秒，默认 300 秒)
        
        Returns:
            执行结果字典
        """
        start_time = time.time()
        steps = []
        rounds_timing = []  # 记录每轮的详细时间
        # Token 消耗累计器
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        thoughts = []  # 记录所有思考过程，用于生成失败总结
        
        try:
            # 从主目录导入统一的 GUIAgent
            from parallel_agents.gui_agent import GUIAgent
            print(f"[DEBUG] GUIAgent loaded successfully with GPT support")
            
            # 创建 GUIAgent 实例（GPT 模式）
            _api_config = get_api_config("deerapi")
            # 创建反思总结用的 OpenAI 兼容客户端
            from openai import OpenAI as _OpenAI
            _reflection_client = _OpenAI(
                api_key=_api_config["api_key"],
                base_url=_api_config["base_url"]
            )
            _reflection_model = "gpt-5.2"
            runtime_conf = {
                "gpt_api_key": _api_config["api_key"],
                "base_url": _api_config["base_url"],
                "gpt_model_name": "gpt-5.2",
                "temperature": 0.0,
                "max_tokens": 2000,
                "history_n": 5,
                "language": "English"
            }
            
            agent = GUIAgent(
                platform="ubuntu",
                action_space="pyautogui",
                observation_type="screenshot",
                max_trajectory_length=max_rounds,
                model_type="gpt",  # 使用 GPT 模式
                runtime_conf=runtime_conf
            )
            
            print(f"\n[GPT GUI Agent] Starting task: {task[:100]}...")
            print(f"[GPT GUI Agent] Max rounds: {max_rounds}, Timeout: {timeout}s")
            
            # 执行循环
            round_count = 0
            final_result = ""
            
            while round_count < max_rounds:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    failure_summary = self._generate_failure_summary(steps, thoughts, "timeout", round_count, max_rounds)
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Timeout after {timeout}s. {failure_summary}",
                        "rounds_timing": rounds_timing,
                        "model_name": "gpt-5.2",
                        "gui_token_usage": token_usage
                    }
                
                round_count += 1
                round_start = time.time()  # 记录本轮开始时间
                print(f"\n{'='*60}")
                print(f"[GPT GUI Agent] Round {round_count}/{max_rounds}")
                print(f"{'='*60}")
                
                # 获取截图
                try:
                    screenshot = self.controller.get_screenshot()
                    if screenshot is None:
                        return {
                            "status": "failure",
                            "result": "",
                            "steps": steps,
                            "error": "Screenshot capture failed",
                            "rounds_timing": rounds_timing,
                            "model_name": "gpt-5.2",
                            "gui_token_usage": token_usage
                        }
                    obs = {"screenshot": screenshot}
                except Exception as e:
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Failed to get screenshot: {str(e)}",
                        "rounds_timing": rounds_timing,
                        "model_name": "gpt-5.2",
                        "gui_token_usage": token_usage
                    }
                
                # 调用 agent.predict()
                think_start = time.time()
                try:
                    thought, actions, code = agent.predict(
                        instruction=task,
                        obs=obs
                    )
                    think_end = time.time()

                    # 尝试从 agent 获取 token usage
                    last_usage = getattr(agent, 'last_token_usage', None)
                    if last_usage:
                        token_usage["prompt_tokens"] += last_usage.get("prompt_tokens", 0)
                        token_usage["completion_tokens"] += last_usage.get("completion_tokens", 0)
                        token_usage["total_tokens"] += last_usage.get("total_tokens", 0)

                    # 显示完整的 thought（不截断）
                    # 如果 thought 为空或只有占位符，显示提示信息
                    if thought and thought.strip() and thought != "[tool_calls]":
                        print(f"\n[Thought]:")
                        print(thought)
                        thoughts.append(thought)  # 记录思考过程
                    else:
                        print(f"\n[Thought]: (GPT returned tool call without reasoning text)")
                    
                    print(f"\n[Action]: {code[:100]}..." if len(code) > 100 else f"\n[Action]: {code}")
                    
                    # 执行动作开始（action_start）
                    action_start = think_end
                    
                    step_info = f"Round {round_count}: {actions[0] if actions else 'no action'}"
                    steps.append(step_info)
                    
                    # 检查是否完成
                    if "DONE" in actions or "DONE" in code:
                        action_end = time.time()
                        
                        # 记录轮次时间信息（包含thought和code）
                        rounds_timing.append({
                            "round": round_count,
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
                            "status": "success"
                        })
                        
                        # 提取结果
                        if "DONE:" in code:
                            final_result = code.split("DONE:", 1)[1].strip()
                        else:
                            final_result = thought
                        
                        print(f"\n{'='*60}")
                        print(f"[GPT GUI Agent] ✓ Task completed in {round_count} rounds")
                        print(f"{'='*60}")
                        print(f"[Result]: {final_result}")

                        # 调用基类反思总结
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "success",
                            client=_reflection_client, model_name=_reflection_model,
                        )
                        return {
                            "status": "success",
                            "result": reflection,
                            "steps": steps,
                            "error": "",
                            "rounds_timing": rounds_timing,
                            "model_name": "gpt-5.2",
                            "gui_token_usage": token_usage
                        }
                    
                    # 如果是 WAIT，继续下一轮
                    if "WAIT" in actions or "WAIT" in code:
                        print(f"[Status]: Waiting...")
                        time.sleep(1)
                        continue
                    
                    # 执行代码
                    if code and code not in ["DONE", "WAIT", "FAIL", ""]:
                        print(f"[Status]: Executing action...")
                        
                        try:
                            self.controller.execute_python_command(code)
                            # 增加等待时间，让GUI操作有时间完成
                            # 点击应用图标需要等待启动，输入需要等待响应
                            time.sleep(2.0)
                            action_end = time.time()
                            print(f"[Status]: Action completed")
                        except Exception as exec_error:
                            action_end = time.time()
                            print(f"[Error]: Execution failed - {exec_error}")
                            steps.append(f"Round {round_count}: Execution error")
                    else:
                        action_end = time.time()
                    
                    # 记录轮次时间信息（包含thought和code）
                    rounds_timing.append({
                        "round": round_count,
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
                        "status": "running"
                    })
                    
                except Exception as pred_error:
                    print(f"[GPT GUI Agent] Prediction error: {pred_error}")
                    import traceback
                    traceback.print_exc()
                    steps.append(f"Round {round_count}: Prediction error")
            
            # 达到最大轮次 - 调用基类反思总结
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                client=_reflection_client, model_name=_reflection_model,
            )
            return {
                "status": "failure",
                "result": reflection,
                "steps": steps,
                "error": f"Reached maximum rounds ({max_rounds}) without completing the task.",
                "rounds_timing": rounds_timing,
                "model_name": "gpt-5.2",
                "gui_token_usage": token_usage
            }
            
        except Exception as e:
            import traceback
            error_msg = f"GPT GUI Agent error: {str(e)}"
            print(f"\n[ERROR] {error_msg}")
            
            return {
                "status": "failure",
                "result": "",
                "steps": steps,
                "error": error_msg,
                "rounds_timing": rounds_timing,
                "model_name": "gpt-5.2",
                "gui_token_usage": token_usage
            }
    
    def _generate_failure_summary(self, steps: list, thoughts: list, failure_type: str, current_round: int, max_rounds: int) -> str:
        """
        生成失败原因总结，帮助 Plan Agent 理解失败原因
        
        Args:
            steps: 执行步骤列表
            thoughts: 思考过程列表
            failure_type: 失败类型
            current_round: 当前轮次
            max_rounds: 最大轮次
        
        Returns:
            失败原因总结字符串
        """
        summary_parts = []
        
        summary_parts.append(f"GUI Agent executed {current_round}/{max_rounds} rounds without completing the task.")
        
        # 提取最后几步的思考过程
        recent_thoughts = thoughts[-3:] if len(thoughts) >= 3 else thoughts
        if recent_thoughts:
            summary_parts.append("\nLast attempts (agent's reasoning):")
            for i, thought in enumerate(recent_thoughts):
                # 截取关键部分
                thought_preview = thought[:200] if len(thought) > 200 else thought
                summary_parts.append(f"  - {thought_preview}...")
        
        # 分析可能的失败模式
        all_thoughts = " ".join(thoughts).lower()
        if "click" in all_thoughts and thoughts.count("click") > 5:
            summary_parts.append("\nPossible issue: Agent may be repeatedly clicking without progress.")
        if "navigate" in all_thoughts or "folder" in all_thoughts:
            summary_parts.append("\nPossible issue: Agent may be stuck navigating the file system.")
        if "not found" in all_thoughts or "cannot find" in all_thoughts:
            summary_parts.append("\nPossible issue: Target element not found on screen.")
        
        return "\n".join(summary_parts)
