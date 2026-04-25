"""
GPT-5.4 GUI Agent as Tool
将 GPT54Agent 封装为可被 Plan Agent 调用的工具。

使用 OpenAI Responses API 的 computer-use 功能，
通过 DeerAPI 代理调用 gpt-5.4-mini 模型。

支持两种上下文管理模式:
- 有状态模式 (use_response_id=True, 默认):
  通过 previous_response_id 让服务端维护完整对话历史，与 OSWorld 原版一致。
- 无状态模式 (use_response_id=False):
  每轮独立请求，可通过 max_images 控制携带的历史截图数量。

核心流程:
    1. 获取截图 → 2. 调用 predict() → 3. 执行 pyautogui 动作
    → 4. 获取新截图 → 5. record_step_output() 反馈给 agent
    → 6. 循环直到完成或超时

依赖:
    - parallel_agents/gpt54_gui_agent.py（核心 Agent 逻辑）
    - config/api_config.py（API 配置）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, List, Optional
import time
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config, get_model_name


class GPT54GUIAgentTool(BaseAgentTool):
    """
    GPT-5.4 GUI Agent 工具封装

    封装 GPT54Agent，提供与其他 GUI Agent Tool 一致的 execute() 接口。
    支持两种模式:
    - 被 Plan Agent 作为工具调用
    - gui_only 模式下独立执行完整任务

    输入:
        controller: PythonController 实例（VM 连接）
        prompt_mode: prompt 模式（"tool" 或 "gui_only"，当前 GPT-5.4 无区别）
        use_response_id: 是否使用 previous_response_id 有状态模式（默认 True）
        max_images: 保留的历史截图数量（None=全部/不限制，N=最近N张）
    """

    def __init__(self, controller, prompt_mode: str = "tool",
                 use_response_id: bool = True,
                 max_images: Optional[int] = None):
        """
        初始化 GPT-5.4 GUI Agent Tool

        输入:
            controller: PythonController 实例
            prompt_mode: prompt 模式选择（预留，GPT-5.4 使用统一 prompt）
            use_response_id: 是否使用 previous_response_id 有状态模式（默认 True）
            max_images: 保留的历史截图数量（None=全部，N=最近N张）
        """
        super().__init__(controller)
        self.prompt_mode = prompt_mode
        self.use_response_id = use_response_id
        self.max_images = max_images

    def execute(self, task: str, max_rounds: int = 50, timeout: int = 3600) -> Dict:
        """
        执行基于 GUI 的任务（使用 GPT-5.4 模型）

        输入:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 50）
            timeout: 超时时间（秒，0 表示不限制）

        输出:
            执行结果字典，包含:
            - status: "success" / "failure"
            - result: 任务结果描述
            - steps: 执行步骤列表
            - error: 错误信息
            - rounds_timing: 每轮详细计时
            - model_name: 使用的模型名称
            - gui_token_usage: token 消耗统计
        """
        print(f"\n[GPT54GUIAgentTool] execute() called with task: {task[:100]}...")
        print(f"[GPT54GUIAgentTool] max_rounds={max_rounds}, timeout={timeout}")
        print(f"[GPT54GUIAgentTool] use_response_id={self.use_response_id}, max_images={self.max_images}")

        # ---- 初始化截图保存目录 ----
        from datetime import datetime as _datetime
        _screenshot_dir_env = os.environ.get("GPT54_SCREENSHOT_DIR")
        if _screenshot_dir_env is None:
            _ts = _datetime.now().strftime("%Y%m%d_%H%M%S")
            _screenshot_base = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "..", "logs", "gpt54_screenshots", _ts,
            )
        else:
            _screenshot_base = _screenshot_dir_env
        screenshot_dir = os.path.join(_screenshot_base, "extracted_images")
        os.makedirs(screenshot_dir, exist_ok=True)
        print(f"[GPT54GUIAgentTool] Screenshot directory: {screenshot_dir}")

        start_time = time.time()
        steps: List[Dict] = []
        rounds_timing: List[Dict] = []
        thoughts: List[str] = []
        model_name = get_model_name("gpt54_gui_agent") or "gpt-5.4-mini"
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        try:
            # ---- 获取 API 配置 ----
            api_config = get_api_config("deerapi")

            # ---- 创建 GPT54Agent 实例 ----
            from parallel_agents.gpt54_gui_agent import GPT54Agent
            agent = GPT54Agent(
                model=model_name,
                api_key=api_config["api_key"],
                base_url=api_config["base_url"],
                platform="ubuntu",
                max_trajectory_length=max_rounds,
                client_password="password",
                reasoning_effort="high",
                use_response_id=self.use_response_id,
                max_images=self.max_images,
            )

            mode_str = "有状态(response_id)" if self.use_response_id else "无状态"
            images_str = f"max_images={self.max_images}" if self.max_images else "全部"
            print(f"\n[GPT-5.4 GUI Agent] Starting task: {task[:100]}...")
            print(f"[GPT-5.4 GUI Agent] Model: {model_name}")
            print(f"[GPT-5.4 GUI Agent] Mode: {mode_str}, Images: {images_str}")
            print(f"[GPT-5.4 GUI Agent] Max rounds: {max_rounds}, Timeout: {timeout}s")

            # ---- 执行循环 ----
            round_count = 0
            recent_actions: List[str] = []  # 死循环检测
            MAX_REPEAT = 4

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
                print(f"\n{'=' * 60}")
                print(f"[GPT-5.4 GUI Agent] Round {round_count}/{max_rounds}")
                print(f"{'=' * 60}")

                # ---- 1. 获取截图 ----
                try:
                    screenshot_bytes = self.controller.get_screenshot()
                    if not screenshot_bytes:
                        return {
                            "status": "failure",
                            "result": "",
                            "steps": steps,
                            "error": "Screenshot capture failed",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }

                    # 保存截图
                    _ss_name = f"round_{round_count}_screenshot.png"
                    screenshot_filepath = os.path.join(screenshot_dir, _ss_name)
                    try:
                        with open(screenshot_filepath, 'wb') as _f:
                            _f.write(screenshot_bytes)
                        print(f"        Screenshot saved: {_ss_name}")
                    except Exception as _save_err:
                        print(f"        Warning: Failed to save screenshot: {_save_err}")
                        screenshot_filepath = ""
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

                # ---- 2. 调用模型 ----
                try:
                    inference_start = time.time()
                    predict_info, actions = agent.predict(
                        instruction=task,
                        obs={"screenshot": screenshot_bytes},
                    )
                    inference_end = time.time()
                    inference_time = inference_end - inference_start
                    print(f"        推理耗时: {inference_time:.1f}s")

                    # 累计 token usage
                    usage = predict_info.get("model_usage", {})
                    token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    token_usage["total_tokens"] += (
                        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                    )
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    print(f"  [错误] 模型调用失败: {e}")
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": f"Model error: {str(e)}\n{error_trace}",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }

                # 记录思考/响应内容
                response_text = predict_info.get("response", "")
                thoughts.append(response_text)
                if response_text:
                    display = response_text[:500] + "..." if len(response_text) > 500 else response_text
                    print(f"        [Response] {display}")

                # ---- 3. 检查是否有动作 ----
                if not actions:
                    # 模型没有返回动作 → 可能是任务完成或不可行
                    state_correct = predict_info.get("state_correct", True)
                    round_end = time.time()
                    rounds_timing.append(self._build_round_timing(
                        round_count, round_start, inference_start,
                        inference_end, round_end, "no_action", task,
                        screenshot_path=screenshot_filepath if 'screenshot_filepath' in dir() else "",
                        response_text=response_text,
                        action_details=[],
                    ))
                    steps.append({
                        "round": round_count,
                        "thought": response_text,
                        "actions": [],
                        "action": "no_action",
                        "status": "text_only",
                        "output": response_text,
                        "timestamp": round_end - start_time,
                    })

                    # 区分"模型认为不可行"和"模型给出了文本回答"
                    infeasible = predict_info.get("infeasible", False)

                    if infeasible:
                        # 模型明确声明任务不可行 → failure
                        from openai import OpenAI
                        client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "infeasible",
                            client=client, model_name=model_name,
                        )
                        return {
                            "status": "failure",
                            "result": reflection,
                            "steps": steps,
                            "error": "Agent reported task as infeasible",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }

                    # 模型返回了有实质内容的文本回答（非 infeasible）→ 视为成功完成
                    # 典型场景：QA 任务中模型通过浏览网页后给出文本答案
                    if response_text and len(response_text.strip()) > 20:
                        print(f"      [text_only → success] 模型给出文本回答，视为任务完成")
                        from openai import OpenAI
                        client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "success",
                            client=client, model_name=model_name,
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

                    # 文本过短或空白 → 继续执行，等连续 3 轮无动作再判定完成
                    no_action_count = sum(
                        1 for s in steps[-3:] if s.get("status") == "text_only"
                    )
                    if no_action_count >= 3:
                        from openai import OpenAI
                        client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "success",
                            client=client, model_name=model_name,
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
                    continue

                # ---- 3.5 screenshot-only 重试 ----
                # GPT-5.4 经常先返回 screenshot 动作（"让我看看屏幕"），
                # 需要立即重新调用以获取真正的 GUI 动作。
                # 注意：有状态模式下，重试时 previous_response_id 已设置，
                # computer-use 工具不允许在 input 中直接放 input_image，
                # 因此有状态模式跳过重试，让模型在下一轮自然看到截图。
                is_screenshot_only = all(
                    "time.sleep" in a["action"] and "pyautogui" not in a["action"]
                    for a in actions
                )
                if is_screenshot_only:
                    if self.use_response_id:
                        # 有状态模式：不做重试，通过 record_step_output 反馈截图，
                        # 让下一轮 predict 自然获取
                        print("        [!] 模型请求截图观察（有状态模式），通过反馈截图进入下一轮")
                        try:
                            feedback_ss = self.controller.get_screenshot()
                            if feedback_ss:
                                # 使用最后一个 action 的 call_id 构建 computer_call_output
                                last_cid = actions[-1].get("call_id", "") if actions else ""
                                last_checks = actions[-1].get("pending_checks", []) if actions else []
                                agent.record_step_output(
                                    screenshot_bytes=feedback_ss,
                                    call_id=last_cid,
                                    pending_checks=last_checks,
                                )
                        except Exception as e:
                            print(f"        [!] 反馈截图失败: {e}")
                        continue
                    else:
                        # 无状态模式：可以安全重试
                        print("        [!] 模型请求截图观察，立即重新调用...")
                        try:
                            retry_ss = self.controller.get_screenshot()
                            if retry_ss:
                                retry_info, retry_actions = agent.predict(
                                    instruction=task + "\n\nYou already have the current screenshot above. Take an actual action now (click, type, keypress, etc.), do NOT request another screenshot.",
                                    obs={"screenshot": retry_ss},
                                )
                                retry_usage = retry_info.get("model_usage", {})
                                token_usage["prompt_tokens"] += retry_usage.get("prompt_tokens", 0)
                                token_usage["completion_tokens"] += retry_usage.get("completion_tokens", 0)
                                token_usage["total_tokens"] += (
                                    retry_usage.get("prompt_tokens", 0) + retry_usage.get("completion_tokens", 0)
                                )
                                if retry_actions:
                                    actions = retry_actions
                                    response_text = retry_info.get("response", "")
                                    print(f"        [!] 重新调用成功，获得 {len(actions)} 个动作")
                                else:
                                    print("        [!] 重新调用仍无实际动作，跳过本轮")
                                    continue
                        except Exception as e:
                            print(f"        [!] 重新调用失败: {e}")
                            continue

                # ---- 4. 执行动作 ----
                action_strs = []
                last_call_id = ""
                last_pending_checks = []

                for act in actions:
                    pyautogui_code = act["action"]
                    action_strs.append(pyautogui_code)
                    last_call_id = act.get("call_id", "")
                    last_pending_checks = act.get("pending_checks", [])

                    print(f"        执行: {pyautogui_code[:200]}")

                    try:
                        result = self.controller.execute_python_command(pyautogui_code)
                        if result:
                            print(f"        结果: {str(result)[:200]}")
                    except Exception as e:
                        print(f"        [错误] 执行失败: {e}")

                    # 有状态模式：batch_last 时获取截图并反馈给 agent
                    if act.get("batch_last") and self.use_response_id:
                        try:
                            feedback_ss = self.controller.get_screenshot()
                            if feedback_ss:
                                agent.record_step_output(
                                    screenshot_bytes=feedback_ss,
                                    call_id=last_call_id,
                                    pending_checks=last_pending_checks,
                                )
                                # 保存反馈截图
                                _fb_name = f"round_{round_count}_feedback.png"
                                _fb_path = os.path.join(screenshot_dir, _fb_name)
                                try:
                                    with open(_fb_path, 'wb') as _f:
                                        _f.write(feedback_ss)
                                except Exception:
                                    pass
                        except Exception as e:
                            print(f"        [警告] 反馈截图获取失败: {e}")

                # 无状态模式：记录动作到 agent 历史 + 缓存截图
                if not self.use_response_id:
                    for code in action_strs:
                        agent.record_action(code.split("\n")[-1][:120])
                    # 获取执行后截图并缓存（用于 max_images 手动拼接）
                    if self.max_images is not None and self.max_images > 1:
                        try:
                            post_ss = self.controller.get_screenshot()
                            if post_ss:
                                agent.record_step_output(screenshot_bytes=post_ss)
                        except Exception:
                            pass
                else:
                    # 有状态模式也记录动作摘要（用于日志和死循环检测）
                    for code in action_strs:
                        agent.record_action(code.split("\n")[-1][:120])

                action_end = time.time()

                # 记录本轮 timing
                rounds_timing.append(self._build_round_timing(
                    round_count, round_start, inference_start,
                    inference_end, action_end,
                    " | ".join(a[:80] for a in action_strs) if action_strs else "no_action",
                    task,
                    screenshot_path=screenshot_filepath if 'screenshot_filepath' in dir() else "",
                    response_text=response_text,
                    action_details=action_strs,
                ))

                # 记录步骤
                steps.append({
                    "round": round_count,
                    "thought": response_text,
                    "actions": action_strs,
                    "action": " | ".join(action_strs) if action_strs else "",
                    "status": "executed",
                    "output": response_text,
                    "timestamp": action_end - start_time,
                })

                # ---- 死循环检测 ----
                action_signature = " | ".join(sorted(a[:100] for a in action_strs)) if action_strs else "no_action"
                recent_actions.append(action_signature)
                if len(recent_actions) >= MAX_REPEAT:
                    last_n = recent_actions[-MAX_REPEAT:]
                    if len(set(last_n)) == 1 and last_n[0] != "no_action":
                        print(f"  [WARN] 检测到连续 {MAX_REPEAT} 次重复动作: {last_n[0][:100]}")
                        from openai import OpenAI
                        client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "repeat_loop",
                            client=client, model_name=model_name,
                        )
                        return {
                            "status": "failure",
                            "result": reflection,
                            "steps": steps,
                            "error": f"Detected {MAX_REPEAT} repeated actions, stopping.",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }

                # 步间等待
                time.sleep(1.5)

            # ---- 达到最大轮次 ----
            from openai import OpenAI
            client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                client=client, model_name=model_name,
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
                "gui_token_usage": token_usage,
                "error": f"Unexpected error: {str(e)}\n{error_trace}",
                "rounds_timing": rounds_timing,
                "model_name": model_name,
            }

    def _build_round_timing(
        self,
        round_num: int,
        round_start: float,
        think_start: float,
        think_end: float,
        action_end: float,
        action_str: str,
        task: str,
        screenshot_path: str = "",
        response_text: str = "",
        action_details: list = None,
    ) -> Dict:
        """
        构建与 ExecutionRecorder 兼容的单轮 timing 字典

        输入:
            round_num: 轮次序号
            round_start: 本轮开始时间戳
            think_start: 推理开始时间戳
            think_end: 推理结束时间戳
            action_end: 动作执行结束时间戳
            action_str: 动作描述
            task: 任务描述
            screenshot_path: 截图文件路径
            response_text: 模型完整响应文本
            action_details: 结构化动作列表

        输出:
            timing 字典
        """
        return {
            "round": round_num,
            "duration": action_end - round_start,
            "think_start": think_start,
            "think_end": think_end,
            "action_start": think_end,
            "action_end": action_end,
            "action": action_str,
            "response_text": response_text,
            "action_details": action_details or [],
            "messages": [
                {"role": "system", "content": "(GPT-5.4 Responses API)"},
                {"role": "user", "content": task},
            ],
            "screenshot_url": screenshot_path,
            "timing": {
                "preparation": max(0.0, think_start - round_start),
                "api_call": max(0.0, think_end - think_start),
                "parsing_and_execution": max(0.0, action_end - think_end),
            },
        }

    def _generate_failure_summary(
        self,
        steps: List[Dict],
        thoughts: List[str],
        failure_reason: str,
        current_round: int,
        max_rounds: int,
    ) -> str:
        """
        生成失败总结

        输入:
            steps: 已执行的步骤列表
            thoughts: 思考过程列表
            failure_reason: 失败原因
            current_round: 当前轮次
            max_rounds: 最大轮次

        输出:
            失败总结文本
        """
        summary_parts = [
            f"Completed {current_round}/{max_rounds} rounds before {failure_reason}."
        ]

        if thoughts:
            last_thoughts = thoughts[-3:]
            summary_parts.append("\nLast thoughts:")
            for i, thought in enumerate(last_thoughts, 1):
                summary_parts.append(f"  {i}. {thought[:100]}...")

        if steps:
            last_steps = steps[-3:]
            summary_parts.append("\nLast actions:")
            for step in last_steps:
                actions_str = ", ".join(step.get("actions", [])[:2])
                summary_parts.append(f"  Round {step['round']}: {actions_str[:100]}")

        return "\n".join(summary_parts)
