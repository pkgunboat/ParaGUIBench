"""
Doubao GUI Agent as Tool
将 DoubaoSeedGUIAgent 封装为可被 Plan Agent 调用的工具
使用官方 OSWorld Doubao Seed 实现，基于 volcenginesdkarkruntime.Ark
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
import time
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config, get_model_name


class DoubaoGUIAgentTool(BaseAgentTool):
    """Doubao Seed GUI Agent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 15, timeout: int = 600) -> Dict:
        """
        执行基于 GUI 的任务（使用 Doubao Seed）
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 15 轮）
            timeout: 超时时间(秒，默认 600 秒)
        
        Returns:
            执行结果字典
        """
        print(f"\n[DoubaoGUIAgentTool] execute() called with task: {task[:100]}...")
        print(f"[DoubaoGUIAgentTool] max_rounds={max_rounds}, timeout={timeout}")
        
        start_time = time.time()
        steps = []
        rounds_timing = []  # 记录每轮的详细时间
        thoughts = []  # 记录所有思考过程，用于生成失败总结
        # 默认使用 Seed 1.8；可通过 config/api_config.py 的 DEFAULT_MODELS["doubao_gui_agent"] 覆盖
        model_name = get_model_name("doubao_gui_agent")
        # Token 消耗累计器
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        
        try:
            # 导入官方 DoubaoSeedGUIAgent 与 prompt 常量
            from parallel_agents.doubao_seed_gui_agent import DoubaoSeedGUIAgent, SYSTEM_PROMPT, FUNCTION_DEFINITION
            print(f"[DEBUG] DoubaoSeedGUIAgent loaded successfully")
            
            # 从统一配置获取 Doubao API 信息（火山引擎官方端点）
            doubao_config = get_api_config("doubao")
            
            # 创建 DoubaoSeedGUIAgent 实例
            runtime_conf = {
                "doubao_api_key": doubao_config["api_key"],
                "doubao_base_url": doubao_config["base_url"],
                "doubao_model_name": model_name,
                "temperature": 0.3,   # 官方推荐的温度
                "max_tokens": 4096,
                "top_p": 0.95,
            }
            
            model_name = runtime_conf.get("doubao_model_name", model_name)
            
            agent = DoubaoSeedGUIAgent(
                platform="ubuntu",
                model_type="doubao",
                max_trajectory_length=max_rounds,
                history_n=3,  # 官方默认保留3张历史图片
                runtime_conf=runtime_conf,
                resize_image=False,  # 默认不resize
                controller=self.controller,
                execute_actions=True
            )
            
            print(f"\n[Doubao Seed GUI Agent] Starting task: {task[:100]}...")
            print(f"[Doubao Seed GUI Agent] Model: {model_name}")
            print(f"[Doubao Seed GUI Agent] Max rounds: {max_rounds}, Timeout: {timeout}s")
            
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
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                round_count += 1
                round_start = time.time()  # 记录本轮开始时间
                print(f"\n{'='*60}")
                print(f"[Doubao Seed GUI Agent] Round {round_count}/{max_rounds}")
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
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }
                    obs = {"screenshot": screenshot}
                except Exception as e:
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Screenshot error: {str(e)}",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                # 调用 predict
                # DoubaoSeedGUIAgent.predict 返回: (prediction_text, actions_list)
                try:
                    prediction, actions = agent.predict(task, obs)

                    # 累计 token usage（从 Agent 的 last_token_usage 属性读取）
                    last_usage = getattr(agent, 'last_token_usage', {})
                    token_usage["prompt_tokens"] += last_usage.get("prompt_tokens", 0)
                    token_usage["completion_tokens"] += last_usage.get("completion_tokens", 0)
                    token_usage["total_tokens"] += last_usage.get("total_tokens", 0)

                    # 提取thought（从prediction中分离）
                    if "</think_never_used_51bce0c785ca2f68081bfa7d91973934>" in prediction:
                        thought = prediction.split("</think_never_used_51bce0c785ca2f68081bfa7d91973934>")[0]
                        thought = thought.replace("<think_never_used_51bce0c785ca2f68081bfa7d91973934>", "").strip()
                    else:
                        thought = prediction  # 显示完整内容
                    
                    # 记录思考过程
                    thoughts.append(thought)
                    
                    # 显示完整的thought和actions
                    print(f"\n[THOUGHT - FULL]")
                    print(f"{'='*60}")
                    print(thought)
                    print(f"{'='*60}")
                    print(f"\n[ACTIONS] {actions}")
                    print(f"\n[PREDICTION - FULL]")
                    print(f"{'='*60}")
                    print(prediction)
                    print(f"{'='*60}")
                    
                    # 如果返回 None/空或者特殊标记，可能遇到了错误
                    if not prediction and not actions:
                        return {
                            "status": "failure",
                            "result": "",
                            "steps": steps,
                            "error": "Model returned empty response",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }
                    
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    print(f"[ERROR] Prediction error: {e}")
                    print(error_trace)
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Prediction error: {str(e)}\n{error_trace}",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                # 记录本轮耗时
                round_end = time.time()
                round_time = round_end - round_start
                
                # 组装与 ExecutionRecorder 兼容的 timing 信息
                last_timing = agent.last_round_timing or {}
                think_start = last_timing.get("think_start", round_start)
                think_end = last_timing.get("think_end", round_end)
                action_start = last_timing.get("action_start", think_end)
                action_end = last_timing.get("action_end", round_end)
                
                # 获取原始 content（用于排查字符丢失问题：区分模型端截断 vs 解析端丢失）
                raw_content = getattr(agent, 'last_raw_content', '')

                rounds_timing.append({
                    "round": round_count,
                    "duration": round_time,
                    "think_start": think_start,
                    "think_end": think_end,
                    "action_start": action_start,
                    "action_end": action_end,
                    "action": " | ".join(actions) if isinstance(actions, list) else str(actions),
                    "response_text": thought if thought else "",
                    "action_details": actions if isinstance(actions, list) else [],
                    "raw_content": raw_content,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "system", "content": FUNCTION_DEFINITION},
                        {"role": "user", "content": task}
                    ],
                    "screenshot_url": "",
                    "timing": {
                        "preparation": 0,
                        "api_call": max(0.0, (think_end - think_start)),
                        "parsing_and_execution": max(0.0, (action_end - action_start))
                    }
                })
                
                # 记录步骤
                step_info = {
                    "round": round_count,
                    "thought": thought[:100] if thought else "",
                    "actions": actions if isinstance(actions, list) else [str(actions)],
                    "action": " | ".join(actions) if isinstance(actions, list) else str(actions),
                    "timestamp": round_end - start_time
                }
                step_info["status"] = "executed"
                step_info["output"] = thought[:300] if thought else ""
                steps.append(step_info)
                
                # 检查是否完成
                if "DONE" in actions or "finished" in str(actions).lower():
                    print(f"\n[SUCCESS] Task completed in {round_count} rounds")
                    # 调用基类反思总结（附带最后一张截图）
                    last_img = agent.history_images[-1] if agent.history_images else ""
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        last_screenshot_b64=last_img,
                        client=agent.ark_client, model_name=agent.model_name,
                    )
                    return {
                        "status": "success",
                        "result": reflection,
                        "steps": steps,
                        "error": "",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                # 检查是否需要等待
                if "WAIT" in actions:
                    print(f"[INFO] Waiting...")
                    time.sleep(1)
                    continue
                
                # 检查是否失败
                if "FAIL" in actions or "error" in str(actions).lower():
                    failure_summary = self._generate_failure_summary(steps, thoughts, "execution_error", round_count, max_rounds)
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Agent execution failed. {failure_summary}",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                # 动作执行已在 DoubaoSeedGUIAgent.predict() 内部完成
            
            # 达到最大轮次 - 调用基类反思总结（附带最后一张截图）
            last_img = agent.history_images[-1] if agent.history_images else ""
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                last_screenshot_b64=last_img,
                client=agent.ark_client, model_name=agent.model_name,
            )

            return {
                "status": "failure",
                "result": reflection,
                "steps": steps,
                "error": f"Reached maximum rounds ({max_rounds}) without completing the task.",
                "rounds_timing": rounds_timing,
                "model_name": model_name,
                "gui_token_usage": token_usage,
            }

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"[ERROR] Unexpected error: {e}")
            print(error_trace)
            return {
                "status": "failure",
                "result": "",
                "steps": steps,
                "error": f"Unexpected error: {str(e)}\n{error_trace}",
                "rounds_timing": rounds_timing,
                "model_name": model_name,
                "gui_token_usage": token_usage,
            }
    
    def _generate_failure_summary(self, steps: list, thoughts: list, failure_reason: str, current_round: int, max_rounds: int) -> str:
        """
        生成失败总结，帮助调试
        
        Args:
            steps: 已执行的步骤列表
            thoughts: 思考过程列表
            failure_reason: 失败原因 ("timeout", "max_rounds", "execution_error")
            current_round: 当前轮次
            max_rounds: 最大轮次
        
        Returns:
            str: 失败总结文本
        """
        summary_parts = []
        
        # 基本信息
        summary_parts.append(f"Completed {current_round}/{max_rounds} rounds before {failure_reason}.")
        
        # 最后几步的思考
        if thoughts:
            last_thoughts = thoughts[-3:]  # 最后3个思考
            summary_parts.append("\nLast thoughts:")
            for i, thought in enumerate(last_thoughts, 1):
                summary_parts.append(f"  {i}. {thought[:100]}...")
        
        # 最后几步的动作
        if steps:
            last_steps = steps[-3:]  # 最后3步
            summary_parts.append("\nLast actions:")
            for step in last_steps:
                actions_str = ", ".join(step.get("actions", []))
                summary_parts.append(f"  Round {step['round']}: {actions_str}")
        
        return "\n".join(summary_parts)
