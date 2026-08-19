"""
Kimi GUI Agent as Tool
将基于 OSWorld 官方实现的 KimiGUIAgent 封装为可被 Plan Agent 调用的工具

核心改动（相比旧版）：
- 不再套壳 ClaudeComputerUseAgent，改为使用独立的 KimiGUIAgent
- 使用 Kimi K2.5 专用 Prompt（Thought → Action → Code 三段式）
- 支持 thinking（reasoning_content）模式
- 支持从 computer.terminate(answer="xxx") 提取 QA 任务答案
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, List
import time
import logging
import traceback
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config, get_model_name

logger = logging.getLogger(__name__)


class KimiGUIAgentTool(BaseAgentTool):
    """
    Kimi GUI Agent 工具封装

    基于 OSWorld 官方 KimiAgent 适配，使用 Kimi K2.5 专用 Prompt 和解析逻辑。
    通过 BaseAgentTool 继承获得 controller 和 format_result 能力。
    """

    def execute(self, task: str, max_rounds: int = 15, timeout: int = 0) -> Dict:
        """
        执行基于 GUI 的任务（使用 Kimi K2.5）

        执行流程：
        1. 从 config 获取 Kimi API 配置
        2. 创建 KimiGUIAgent 实例（注入 controller）
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
                    "model_name": "kimi-k2.5"
                }
        """
        logger.info("[TRACE][KimiGUIAgentTool] ====== execute() 入口 ======")
        logger.info("[TRACE][KimiGUIAgentTool] task: %s", task[:100])
        logger.info("[TRACE][KimiGUIAgentTool] max_rounds=%d, timeout=%d", max_rounds, timeout)

        start_time = time.time()
        steps: List[Dict] = []
        rounds_timing: List[Dict] = []
        thoughts: List[str] = []
        model_name = get_model_name("kimi_gui_agent")
        # Token 消耗累计器
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        try:
            # 导入 KimiGUIAgent
            t0 = time.time()
            from parallel_agents.kimi_gui_agent import KimiGUIAgent
            logger.info("[TRACE][KimiGUIAgentTool] KimiGUIAgent 模块导入完成 (%.2fs)", time.time() - t0)

            # 从统一配置获取 Kimi API 信息
            kimi_config = get_api_config("kimi")
            logger.info("[TRACE][KimiGUIAgentTool] API 配置: base_url=%s", kimi_config["base_url"])

            # 创建 KimiGUIAgent 实例
            t0 = time.time()
            agent = KimiGUIAgent(
                model=model_name,
                max_steps=max_rounds,
                max_image_history_length=3,
                platform="ubuntu",
                max_tokens=4096,
                top_p=0.95,
                temperature=0,  # 确定性约束：所有 LLM 调用 temperature=0
                screen_size=(1920, 1080),
                coordinate_type="relative",
                password="password",
                thinking=True,
                api_key=kimi_config["api_key"],
                base_url=kimi_config["base_url"],
                controller=self.controller,
                execute_actions=True,
            )
            logger.info("[TRACE][KimiGUIAgentTool] KimiGUIAgent 实例创建完成 (%.2fs)", time.time() - t0)

            # 创建反思总结用的 OpenAI 兼容客户端（复用 Kimi API 配置）
            from openai import OpenAI as _OpenAI
            _reflection_client = _OpenAI(
                api_key=kimi_config["api_key"],
                base_url=kimi_config["base_url"]
            )
            _reflection_model = model_name

            logger.info("[TRACE][KimiGUIAgentTool] 开始执行任务循环 (model=%s, max_rounds=%d, timeout=%ds)",
                        model_name, max_rounds, timeout)

            # 执行循环
            round_count = 0
            final_answer = None  # QA 任务的 answer

            while round_count < max_rounds:
                elapsed = time.time() - start_time
                if timeout > 0 and elapsed > timeout:
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
                logger.info("[TRACE][KimiGUIAgentTool] ====== Round %d/%d 开始 (总耗时 %.1fs) ======",
                            round_count, max_rounds, round_start - start_time)

                # 获取截图
                try:
                    t0 = time.time()
                    screenshot = self.controller.get_screenshot()
                    logger.info("[TRACE][KimiGUIAgentTool] 截图获取完成 (%.2fs, %s)",
                                time.time() - t0,
                                f"{len(screenshot)} bytes" if screenshot else "None")
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
                # KimiGUIAgent.predict 返回: (response_dict, pyautogui_actions, sections)
                try:
                    t0 = time.time()
                    logger.info("[TRACE][KimiGUIAgentTool] >>> predict() 调用开始")
                    response, actions, sections = agent.predict(
                        task, obs, step_idx=round_count
                    )
                    logger.info("[TRACE][KimiGUIAgentTool] <<< predict() 调用结束 (%.2fs), actions=%s",
                                time.time() - t0, actions)

                    # 累计 token usage
                    last_usage = getattr(agent, 'last_token_usage', {})
                    token_usage["prompt_tokens"] += last_usage.get("prompt_tokens", 0)
                    token_usage["completion_tokens"] += last_usage.get("completion_tokens", 0)
                    token_usage["total_tokens"] += last_usage.get("total_tokens", 0)

                    # 提取 thought
                    thought = sections.get('thought', '')
                    action_desc = sections.get('action', '')
                    code = sections.get('code', '')

                    # 记录思考过程
                    thoughts.append(thought)

                    # 提取 answer（QA 任务）
                    if sections.get('answer'):
                        final_answer = sections['answer']

                    # 显示日志
                    logger.info("[TRACE][KimiGUIAgentTool] [THOUGHT] %s",
                                thought[:300] if thought else "(empty)")
                    logger.info("[TRACE][KimiGUIAgentTool] [ACTION] %s", action_desc)
                    logger.info("[TRACE][KimiGUIAgentTool] [CODE] %s", code[:200] if code else "(empty)")

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
                    logger.error("[TRACE][KimiGUIAgentTool] predict() 异常: %s\n%s", e, error_trace)
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
                    logger.info("[TRACE][KimiGUIAgentTool] 任务完成 (round=%d, 总耗时=%.1fs)",
                                round_count, time.time() - start_time)
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
                    logger.info("[TRACE][KimiGUIAgentTool] WAIT 动作，等待 1s...")
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

                # 动作执行已在 KimiGUIAgent.predict() 内部完成（execute_actions=True）
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
            logger.error("[TRACE][KimiGUIAgentTool] execute() 未预期异常: %s\n%s", e, error_trace)
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
