"""
Qwen GUI Agent as Tool
将基于 OSWorld 官方实现的 Qwen3GUIAgent 封装为可被 Plan Agent 调用的工具

核心改动（相比旧版）：
- 不再使用 Mode 1（GUIAgent + qwen_adapter），改为 Mode 2（独立 Agent 直接封装）
- 使用 Qwen3VL 专用 Prompt（Action → <tool_call> XML 格式）
- 通过 OpenAI 兼容后端调用 Qwen3-VL（默认 DeerAPI，可切换 dashscope）
- 支持 QA 答案提取（answer action / terminate answer_text）
- 返回值包含 rounds_timing 和 model_name
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, List
import time
import traceback
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config, get_model_name


class QwenGUIAgentTool(BaseAgentTool):
    """
    Qwen GUI Agent 工具封装

    基于 OSWorld 官方 Qwen3VLAgent 适配，使用 OpenAI 兼容后端调用 Qwen3-VL。
    通过 BaseAgentTool 继承获得 controller 和 format_result 能力。

    支持两种使用场景：
    - Plan Agent 模式：作为 GUI 子 Agent 被 Plan Agent 调用
    - gui_only 模式：作为独立 baseline Agent 直接完成完整任务
    """

    def __init__(
        self,
        controller,
        model_name: str = None,
        prompt_mode: str = "tool",
        api_provider: str = "deerapi",
    ):
        """
        初始化 Qwen GUI Agent Tool

        Args:
            controller: PythonController 实例（VM 交互）
            model_name: 模型名（如 "qwen3-vl"、"qwen3-vl-235b-a22b"）。
                        为 None 时回退到 get_model_name("qwen_gui_agent")，
                        即环境变量 BENCH_DEFAULT_QWEN_GUI_AGENT 或默认值 "qwen3-vl"。
            prompt_mode: prompt 模式选择（"tool" | "gui_only"）。
                         对齐 Seed18/GPT54 接口；当前 Qwen3VL 的内置 prompt
                         已具备独立完成任务能力，两种模式无差异，参数仅用于签名一致性。
            api_provider: API 来源（默认 "deerapi"，可选 "dashscope"）。
                          决定从 get_api_config() 取哪一组 api_key / base_url。
        """
        super().__init__(controller)
        self.model_name = model_name
        self.prompt_mode = prompt_mode
        self.api_provider = api_provider

    def execute(self, task: str, max_rounds: int = 15, timeout: int = 600) -> Dict:
        """
        执行基于 GUI 的任务（使用 Qwen3-VL）

        执行流程：
        1. 从 config 获取 API 配置（默认 deerapi，可由 self.api_provider 切换）
        2. 创建 Qwen3GUIAgent 实例（注入 controller）
        3. 循环执行 predict()，每轮获取截图 → 调用模型 → 解析动作 → 执行
        4. 根据 DONE/WAIT/FAIL 状态返回结果

        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 15 轮）
            timeout: 超时时间（秒，默认 600 秒 = 10 分钟）

        Returns:
            Dict: 执行结果字典，格式：
                {
                    "status": "success" | "failure",
                    "result": "结果描述或 QA 答案",
                    "steps": [步骤详情列表],
                    "error": "错误信息",
                    "rounds_timing": [每轮计时详情],
                    "model_name": "qwen3-vl"
                }
        """
        print(f"\n[QwenGUIAgentTool] execute() called with task: {task[:100]}...")
        print(f"[QwenGUIAgentTool] max_rounds={max_rounds}, timeout={timeout}")

        start_time = time.time()
        steps: List[Dict] = []
        rounds_timing: List[Dict] = []
        thoughts: List[str] = []
        # 优先使用构造时传入的 model_name，否则回退到环境变量/默认值
        model_name = self.model_name or get_model_name("qwen_gui_agent")

        # Token 消耗累计器
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        try:
            # 导入 Qwen3GUIAgent
            from parallel_agents.qwen3_gui_agent import Qwen3GUIAgent
            print(f"[DEBUG] Qwen3GUIAgent loaded successfully")

            # 从统一配置获取 API 信息（默认走 DeerAPI 网关，亦可切回 dashscope）
            api_cfg = get_api_config(self.api_provider)

            # 创建 Qwen3GUIAgent 实例
            agent = Qwen3GUIAgent(
                model=model_name,
                max_tokens=32768,
                history_n=4,
                coordinate_type="relative",
                api_key=api_cfg["api_key"],
                base_url=api_cfg["base_url"],
                controller=self.controller,
                execute_actions=True,
            )

            # 创建反思总结用的 OpenAI 兼容客户端（复用同一组 API 配置）
            from openai import OpenAI as _OpenAI
            _reflection_client = _OpenAI(
                api_key=api_cfg["api_key"],
                base_url=api_cfg["base_url"]
            )
            _reflection_model = model_name

            print(f"\n[Qwen GUI Agent] Starting task: {task[:100]}...")
            print(f"[Qwen GUI Agent] Model: {model_name} | Provider: {self.api_provider}")
            print(f"[Qwen GUI Agent] Base URL: {api_cfg['base_url']}")
            print(f"[Qwen GUI Agent] Max rounds: {max_rounds}, Timeout: {timeout}s")

            # 执行循环
            round_count = 0
            final_answer = None  # QA 任务的 answer

            while round_count < max_rounds:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    failure_summary = self._generate_failure_summary(
                        steps, thoughts, "timeout", round_count, max_rounds
                    )
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
                round_start = time.time()
                print(f"\n{'='*60}")
                print(f"[Qwen GUI Agent] Round {round_count}/{max_rounds}")
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
                try:
                    response, actions, sections = agent.predict(
                        task, obs, step_idx=round_count
                    )

                    # 累计 token usage
                    last_usage = getattr(agent, 'last_token_usage', {})
                    token_usage["prompt_tokens"] += last_usage.get("prompt_tokens", 0)
                    token_usage["completion_tokens"] += last_usage.get("completion_tokens", 0)
                    token_usage["total_tokens"] += last_usage.get("total_tokens", 0)

                    # 提取 thought（Qwen3VL 的 Action 描述作为 thought）
                    thought = sections.get('action', '')
                    action_desc = sections.get('action', '')

                    # 记录思考过程
                    thoughts.append(thought)

                    # 提取 answer（QA 任务）
                    if sections.get('answer'):
                        final_answer = sections['answer']

                    # 显示日志
                    print(f"\n[ACTION] {action_desc}")
                    print(f"[ACTIONS] {actions}")

                    # 空响应检查
                    if not response and not actions:
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
                    "messages": [],
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
                    "thought": thought[:200] if thought else "",
                    "actions": actions if isinstance(actions, list) else [str(actions)],
                    "action": " | ".join(actions) if isinstance(actions, list) else str(actions),
                    "timestamp": round_end - start_time,
                    "status": "executed",
                    "output": thought[:300] if thought else ""
                }
                steps.append(step_info)

                # 检查是否完成
                if "DONE" in actions:
                    print(f"\n[SUCCESS] Task completed in {round_count} rounds")
                    # 调用基类反思总结
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        client=_reflection_client, model_name=_reflection_model,
                    )
                    # QA 任务优先返回 answer
                    result_content = final_answer if final_answer else reflection
                    return {
                        "status": "success",
                        "result": result_content,
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
                if "FAIL" in actions:
                    failure_summary = self._generate_failure_summary(
                        steps, thoughts, "execution_error", round_count, max_rounds
                    )
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Agent execution failed. {failure_summary}",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }

                # 动作执行已在 Qwen3GUIAgent.predict() 内部完成（execute_actions=True）
                # 等待动作效果生效
                time.sleep(2)

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
                "model_name": model_name,
                "gui_token_usage": token_usage,
            }

        except Exception as e:
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

    def _generate_failure_summary(
        self,
        steps: List[Dict],
        thoughts: List[str],
        failure_reason: str,
        current_round: int,
        max_rounds: int
    ) -> str:
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
            last_thoughts = thoughts[-3:]
            summary_parts.append("\nLast thoughts:")
            for i, thought in enumerate(last_thoughts, 1):
                summary_parts.append(f"  {i}. {thought[:100]}...")

        # 最后几步的动作
        if steps:
            last_steps = steps[-3:]
            summary_parts.append("\nLast actions:")
            for step in last_steps:
                actions_str = ", ".join(step.get("actions", []))
                summary_parts.append(f"  Round {step['round']}: {actions_str}")

        return "\n".join(summary_parts)
