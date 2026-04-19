"""
Seed 1.8 GUI Agent as Tool
将 seed_1_8_gui_test.py 中的 Seed18GUITester 封装为可被 Plan Agent 调用的工具。

与 DoubaoGUIAgentTool 的区别：
- DoubaoGUIAgentTool 封装的是 DoubaoSeedGUIAgent（依赖 ui_tars 的 parsing_response_to_pyautogui_code）
- 本工具封装 Seed18GUITester，具备三层动作解析 fallback（XML → 残片 → tool_calls）
  以及独立的 seed_action_to_pyautogui 转换，不依赖 ui_tars

工具模式下使用增强 prompt（prompts/seed18_gui_agent_prompt.py），包含：
- 角色定位（Plan Agent 调用的工具）
- 明确的终止条件（finished / infeasible）
- 效率规则（找到答案立即返回）
- GUI 操作指南

依赖：
    - parallel_agents/seed_1_8_gui_test.py（核心解析/执行逻辑）
    - prompts/seed18_gui_agent_prompt.py（工具模式增强 prompt）
    - volcenginesdkarkruntime（火山引擎 Ark SDK 直连）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, List, Optional
import time
import io
import base64
from PIL import Image
from .base_agent_tool import BaseAgentTool
from config.api_config import get_api_config, get_model_name

# 从 seed_1_8_gui_test 导入核心组件
from parallel_agents.seed_1_8_gui_test import (
    Seed18GUITester,
    parse_seed_fragment,
    seed_action_to_pyautogui,
    extract_thinking_and_content,
    TERMINAL_ACTIONS,
    GUI_TOOL_SCHEMAS,
    SYSTEM_PROMPT_TOOLS,
)

# 导入工具模式增强 prompt（替换默认的 SYSTEM_PROMPT_ROLE）
from prompts.seed18_gui_agent_prompt import (
    SEED18_TOOL_SYSTEM_PROMPT_ROLE,
    SEED18_USER_PROMPT_FIRST,
    SEED18_USER_PROMPT_CONTINUE,
    SEED18_GUI_ONLY_SYSTEM_PROMPT,
    SEED18_GUI_ONLY_USER_PROMPT_FIRST,
    SEED18_GUI_ONLY_USER_PROMPT_CONTINUE,
)

# 尝试导入 XML 解析器（与 Seed18GUITester 内部一致）
try:
    from utils.xml_action_parser import parse_xml_action_v3
except ImportError:
    parse_xml_action_v3 = None


class Seed18GUIAgentTool(BaseAgentTool):
    """
    Seed 1.8 GUI Agent 工具封装

    复用 seed_1_8_gui_test.py 中的核心逻辑：
    - 流式调用 Seed 1.8 模型（支持 reasoning_content）
    - 三层动作解析 fallback（XML → 残片 → tool_calls）
    - seed_action_to_pyautogui 坐标转换与代码生成

    输入:
        controller: PythonController 实例
        prompt_mode: prompt 模式选择
            - "tool"（默认）：被 Plan Agent 作为工具调用
            - "gui_only"：独立执行完整任务，不经过 Plan Agent
    """

    def __init__(self, controller, prompt_mode: str = "tool"):
        super().__init__(controller)
        self.prompt_mode = prompt_mode

    def execute(self, task: str, max_rounds: int = 15, timeout: int = 0) -> Dict:
        """
        执行基于 GUI 的任务（使用 Seed 1.8 模型）

        输入:
            task: 任务描述
            max_rounds: 最大执行轮次（默认 15 轮）
            timeout: 超时时间（秒，0 表示不限制）
        输出:
            执行结果字典，包含 status/result/steps/error/rounds_timing/model_name
        """
        print(f"\n[Seed18GUIAgentTool] execute() called with task: {task[:100]}...")
        print(f"[Seed18GUIAgentTool] max_rounds={max_rounds}, timeout={timeout}")

        # ---- 初始化截图保存目录 ----
        import os as _os
        from datetime import datetime as _datetime
        _screenshot_dir_env = _os.environ.get("SEED18_SCREENSHOT_DIR")
        if _screenshot_dir_env is None:
            _ts = _datetime.now().strftime("%Y%m%d_%H%M%S")
            _screenshot_base = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "..", "logs", "seed18_screenshots", _ts,
            )
        else:
            _screenshot_base = _screenshot_dir_env
        screenshot_dir = _os.path.join(_screenshot_base, "extracted_images")
        _os.makedirs(screenshot_dir, exist_ok=True)
        print(f"[Seed18GUIAgentTool] Screenshot directory: {screenshot_dir}")
        screenshot_filepath = ""  # 当前轮次的截图路径（每轮更新）

        start_time = time.time()
        steps: List[Dict] = []
        rounds_timing: List[Dict] = []
        thoughts: List[str] = []
        # 模型名称从统一配置读取（通过火山引擎 Ark SDK 直连），便于在 pipeline 层快速切换。
        model_name = get_model_name("seed18_gui_agent") or "doubao-seed-1-8-251228"
        # Token 消耗累计器
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        
        try:
            # ---- 获取 API 配置（火山引擎 OpenAI 兼容端点） ----
            # 改用 sdk="openai"（openai.OpenAI 客户端）以支持 seed 参数；
            # volcenginesdkarkruntime.Ark 不识别 seed，会抛 TypeError。
            ark_config = get_api_config("doubao")

            # ---- 创建 Seed18GUITester 实例（复用其模型调用和消息构建能力） ----
            tester = Seed18GUITester(
                vm_ip=self.controller.vm_ip,
                server_port=int(self.controller.http_server.split(":")[-1]),
                model=model_name,
                api_key=ark_config["api_key"],
                base_url=ark_config["base_url"],
                sdk="openai",  # 豆包 Ark 的 /api/v3 同时是 OpenAI 兼容端点
                max_steps=max_rounds,
                max_tokens=8192,
                temperature=0.0,
                top_p=0.9,
                history_n=5,
                action_pause=0.0,      # 由外层控制节奏，内部不额外等待
                save_screenshots=False,  # 作为工具不需要自行保存截图
            )
            # 替换 controller 为外部传入的实例（确保使用同一个 VM 连接）
            tester.controller = self.controller
            
            print(f"\n[Seed 1.8 GUI Agent] Starting task: {task[:100]}...")
            print(f"[Seed 1.8 GUI Agent] Model: {model_name}")
            print(f"[Seed 1.8 GUI Agent] Max rounds: {max_rounds}, Timeout: {timeout}s")
            
            # ---- 执行循环 ----
            round_count = 0
            recent_actions = []  # 最近 N 轮的 action 签名，用于死循环检测
            MAX_REPEAT = 4  # 连续 N 次相同动作视为死循环

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
                print(f"[Seed 1.8 GUI Agent] Round {round_count}/{max_rounds}")
                print(f"{'=' * 60}")
                
                # ---- 1. 获取截图 ----
                try:
                    screenshot_b64, img_width, img_height = tester._get_screenshot()
                    if screenshot_b64 is None:
                        return {
                            "status": "failure",
                            "result": "",
                            "steps": steps,
                            "error": "Screenshot capture failed",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }
                    tester.history_images.append(screenshot_b64)

                    # ---- 保存截图到磁盘 ----
                    _ss_name = f"round_{round_count}_screenshot.png"
                    screenshot_filepath = _os.path.join(screenshot_dir, _ss_name)
                    try:
                        with open(screenshot_filepath, 'wb') as _f:
                            _f.write(base64.b64decode(screenshot_b64))
                        print(f"        Screenshot saved: {_ss_name}")
                    except Exception as _save_err:
                        print(f"        Warning: Failed to save screenshot: {_save_err}")
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
                
                # ---- 2. 调用模型（带超时重试） ----
                _MAX_API_RETRIES = 3
                _RETRY_DELAYS = [30, 60, 120]
                model_result = None
                for _api_attempt in range(_MAX_API_RETRIES):
                    try:
                        messages = self._build_tool_messages(tester, task, round_count)
                        inference_start = time.time()
                        model_result = tester._call_model(messages)
                        inference_end = time.time()
                        inference_time = inference_end - inference_start
                        print(f"        推理耗时: {inference_time:.1f}s")
                        # 累计 token usage
                        usage = model_result.get('usage', {})
                        token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        token_usage["total_tokens"] += usage.get("total_tokens", 0)
                        break  # 成功则跳出重试
                    except Exception as e:
                        # 判断是否为超时类异常
                        is_timeout = False
                        for cls in type(e).__mro__:
                            if "timeout" in cls.__name__.lower():
                                is_timeout = True
                                break
                        if not is_timeout:
                            is_timeout = "timeout" in str(e).lower()

                        if is_timeout and _api_attempt < _MAX_API_RETRIES - 1:
                            delay = _RETRY_DELAYS[_api_attempt]
                            print(f"  [警告] 模型调用超时，{delay}s 后重试 ({_api_attempt+1}/{_MAX_API_RETRIES}): {e}")
                            time.sleep(delay)
                            continue

                        # 非超时错误或重试耗尽
                        import traceback
                        error_trace = traceback.format_exc()
                        print(f"  [错误] 模型调用失败: {e}")
                        tester.history_images.pop()
                        return {
                            "status": "failure",
                            "result": "",
                            "steps": steps,
                            "error": f"Model error: {str(e)}\n{error_trace}",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                            "gui_token_usage": token_usage,
                        }

                if model_result is None:
                    tester.history_images.pop()
                    return {
                        "status": "failure",
                        "result": "",
                        "steps": steps,
                        "error": "Model call failed after all retries",
                        "rounds_timing": rounds_timing,
                        "model_name": model_name,
                        "gui_token_usage": token_usage,
                    }
                
                # 提取各部分
                thinking = model_result['reasoning_content']
                content_text = model_result['content']
                tool_calls = model_result['tool_calls']
                raw_prediction = model_result['raw_prediction']
                
                tester.history_responses.append(raw_prediction)
                
                # 记录思考过程
                thoughts.append(thinking if thinking else content_text)
                
                # 打印信息
                if thinking:
                    display_thinking = thinking[:500] + "..." if len(thinking) > 500 else thinking
                    print(f"        [Thinking] {display_thinking}")
                if content_text:
                    print(f"        [Content]  {content_text}")
                
                # ---- 3. 解析动作（三层 fallback） ----
                parsed_actions = []
                action_source = ""
                
                # 方式 1：XML 完整解析
                if parse_xml_action_v3 is not None:
                    try:
                        parsed_actions = parse_xml_action_v3(raw_prediction, GUI_TOOL_SCHEMAS)
                        if parsed_actions:
                            action_source = "xml_parse"
                    except Exception:
                        pass
                
                # 方式 2：残片格式解析
                if not parsed_actions and content_text:
                    parsed_actions = parse_seed_fragment(content_text)
                    if parsed_actions:
                        action_source = "fragment_parse"
                
                # 方式 3：API tool_calls
                if not parsed_actions and tool_calls:
                    parsed_actions = tester._parse_tool_calls_to_actions(tool_calls)
                    if parsed_actions:
                        action_source = "tool_calls"
                
                # 没有解析到动作
                if not parsed_actions:
                    print(f"        未解析到任何动作")
                    from parallel_agents.seed_1_8_gui_test import KNOWN_FUNCTIONS
                    has_action_hint = any(f"{fn}>" in content_text for fn in KNOWN_FUNCTIONS)
                    if not has_action_hint and not tool_calls:
                        # 纯文本输出，视为任务结束
                        round_end = time.time()
                        rounds_timing.append(self._build_round_timing(
                            round_count, round_start, inference_start,
                            inference_end, round_end, "text_done", task,
                            screenshot_path=screenshot_filepath,
                            response_text=thinking or content_text,
                            action_details=[],
                        ))
                        steps.append({
                            "round": round_count,
                            "thought": thinking or "",
                            "actions": [],
                            "action": "text_done",
                            "status": "text_only",
                            "output": thinking or content_text,
                            "timestamp": round_end - start_time,
                        })
                        # 调用基类反思总结（附带最后一张截图）
                        last_img = tester.history_images[-1] if tester.history_images else ""
                        reflection = self._generate_reflection_summary(
                            task, steps, thoughts, "success",
                            last_screenshot_b64=last_img,
                            client=tester.client, model_name=tester.model,
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
                    else:
                        # 有痕迹但解析失败，继续
                        continue
                
                print(f"        来源: {action_source}")
                for i, act in enumerate(parsed_actions):
                    print(f"        动作 {i+1}: {act['function']}({act['parameters']})")
                
                # ---- 4. 执行动作 ----
                action_start = time.time()
                should_stop = False
                action_strs = []
                
                for act in parsed_actions:
                    func_name = act['function']
                    
                    # 终止动作
                    if func_name in TERMINAL_ACTIONS:
                        final_content = act['parameters'].get('content', '')
                        print(f"        >>> 终止动作: {func_name}")
                        if final_content:
                            print(f"            内容: {final_content}")
                        should_stop = True
                        
                        round_end = time.time()
                        rounds_timing.append(self._build_round_timing(
                            round_count, round_start, inference_start,
                            inference_end, round_end, func_name, task,
                            screenshot_path=screenshot_filepath,
                            response_text=thinking or content_text,
                            action_details=[func_name],
                        ))
                        steps.append({
                            "round": round_count,
                            "thought": thinking or "",
                            "actions": [func_name],
                            "action": func_name,
                            "status": "terminal",
                            "timestamp": round_end - start_time,
                        })
                        
                        # finished → success，其他终止类型 → 也返回收集到的信息
                        # 调用模型生成执行反思总结，替代原始的最后一轮 thought
                        if func_name == "finished":
                            last_img = tester.history_images[-1] if tester.history_images else ""
                            reflection = self._generate_reflection_summary(
                                task, steps, thoughts, "success",
                                last_screenshot_b64=last_img,
                                client=tester.client, model_name=tester.model,
                            )
                        else:
                            reflection = final_content or thinking or ""
                        return {
                            "status": "success" if func_name == "finished" else "failure",
                            "result": reflection,
                            "steps": steps,
                            "gui_token_usage": token_usage,
                            "error": "" if func_name == "finished" else f"Agent ended with: {func_name}",
                            "rounds_timing": rounds_timing,
                            "model_name": model_name,
                        }
                    
                    # 转换为 pyautogui 代码并执行
                    try:
                        pyautogui_code = seed_action_to_pyautogui(act, img_width, img_height)
                    except (ValueError, TypeError, KeyError) as e:
                        print(f"        [警告] 动作解析失败，跳过: {e}")
                        continue
                    if pyautogui_code is None:
                        continue
                    
                    action_strs.append(pyautogui_code)
                    print(f"        执行: {pyautogui_code}")
                    
                    try:
                        result = self.controller.execute_python_command(pyautogui_code)
                        if result:
                            print(f"        结果: {str(result)[:200]}")
                    except Exception as e:
                        print(f"        [错误] 执行失败: {e}")
                
                action_end = time.time()
                
                # 记录本轮 timing
                rounds_timing.append(self._build_round_timing(
                    round_count, round_start, inference_start,
                    inference_end, action_end,
                    " | ".join(action_strs) if action_strs else "no_action",
                    task,
                    screenshot_path=screenshot_filepath,
                    response_text=thinking or content_text,
                    action_details=action_strs,
                ))

                # 记录步骤
                steps.append({
                    "round": round_count,
                    "thought": thinking or "",
                    "actions": action_strs,
                    "action": " | ".join(action_strs) if action_strs else "",
                    "status": "executed",
                    "output": thinking[:1000] if thinking else "",
                    "timestamp": action_end - start_time,
                })

                # ---- 重复动作死循环检测 ----
                action_signature = " | ".join(sorted(action_strs)) if action_strs else "no_action"
                recent_actions.append(action_signature)
                if len(recent_actions) >= MAX_REPEAT:
                    last_n = recent_actions[-MAX_REPEAT:]
                    if len(set(last_n)) == 1 and last_n[0] != "no_action":
                        print(f"  [WARN] 检测到连续 {MAX_REPEAT} 次重复动作: {last_n[0][:100]}")
                        # 注入提示到下一轮消息，引导 Agent 改变策略
                        tester.history_responses[-1] += (
                            "\n\n[SYSTEM WARNING] You have repeated the same action "
                            f"{MAX_REPEAT} times. This approach is not working. "
                            "Please try a DIFFERENT strategy."
                        )

                # 步间等待
                time.sleep(2.0)
            
            # ---- 达到最大轮次 - 调用基类反思总结（附带最后一张截图） ----
            last_img = tester.history_images[-1] if tester.history_images else ""
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                last_screenshot_b64=last_img,
                client=tester.client, model_name=tester.model,
            )

            # ---- 尝试强制回答（给模型最后一次机会输出答案） ----
            force_answer = None
            try:
                force_msgs = [
                    {"role": "system", "content": "你是一个助手。根据用户提供的任务和已完成步骤，直接给出最终答案。"},
                    {"role": "user", "content": (
                        f"任务: {task}\n\n"
                        f"已完成 {round_count} 轮操作但未在规定轮次内完成任务。\n"
                        f"已执行步骤:\n" + "\n".join(f"- {s}" for s in steps[-10:]) + "\n\n"
                        f"思考过程:\n" + "\n".join(thoughts[-5:]) + "\n\n"
                        "请根据以上信息，直接给出你的最佳答案。"
                        "如果是搜索/查询类任务，给出你目前找到的最佳结果。"
                        "只输出答案本身，不要解释。"
                    )}
                ]
                from parallel_benchmark.utils.llm_determinism import (
                    LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
                )
                _force_kwargs = dict(
                    model=tester.model,
                    messages=force_msgs,
                    max_tokens=512,
                    temperature=LLM_TEMPERATURE,
                    seed=LLM_SEED,
                )
                assert_deterministic(_force_kwargs)
                force_result = tester.client.chat.completions.create(**_force_kwargs)
                force_answer = force_result.choices[0].message.content.strip()
                if force_answer:
                    print(f"[Seed 1.8 GUI Agent] 强制回答: {force_answer[:200]}")
            except Exception as e:
                print(f"[警告] 强制回答调用失败: {e}")

            final_result = force_answer if force_answer else reflection

            return {
                "status": "failure",
                "result": final_result,
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
    
    def _build_tool_messages(
        self,
        tester: 'Seed18GUITester',
        task_instruction: str,
        round_count: int,
    ) -> List[Dict]:
        """
        构建发送给模型的消息列表，根据 self.prompt_mode 选择对应的 prompt 集合。

        - "tool" 模式：使用 SEED18_TOOL_SYSTEM_PROMPT_ROLE（Plan Agent 调用的工具角色）
        - "gui_only" 模式：使用 SEED18_GUI_ONLY_SYSTEM_PROMPT（独立完成任务的主 Agent 角色）

        两种模式的核心内容（终止条件、效率规则、GUI 操作指南）完全一致，
        仅角色定位措辞不同，确保对比实验的公平性。

        输入:
            tester: Seed18GUITester 实例（用于访问 history_images / history_responses）
            task_instruction: 任务描述
            round_count: 当前轮次（1-based），用于决定使用首轮还是后续轮次的 user prompt
        输出:
            OpenAI 格式的消息列表
        """
        # 根据 prompt_mode 选择 prompt 集合
        if self.prompt_mode == "gui_only":
            sys_prompt = SEED18_GUI_ONLY_SYSTEM_PROMPT
            first_prompt = SEED18_GUI_ONLY_USER_PROMPT_FIRST
            continue_prompt = SEED18_GUI_ONLY_USER_PROMPT_CONTINUE
        else:
            sys_prompt = SEED18_TOOL_SYSTEM_PROMPT_ROLE
            first_prompt = SEED18_USER_PROMPT_FIRST
            continue_prompt = SEED18_USER_PROMPT_CONTINUE

        # 系统消息：角色 prompt + Seed 工具定义
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "system", "content": SYSTEM_PROMPT_TOOLS},
        ]

        # 用户消息：首轮包含任务指令，后续轮次用效率提醒
        if round_count == 1:
            user_content = first_prompt.format(instruction=task_instruction)
        else:
            user_content = f"Task: {task_instruction}\n\n{continue_prompt}"
        
        messages.append({"role": "user", "content": user_content})
        
        # 历史消息（与 tester._build_messages 逻辑一致）
        total_rounds = len(tester.history_responses)
        history_img_start = max(0, total_rounds - tester.history_n + 1)
        
        if total_rounds > 0:
            for idx, response_text in enumerate(tester.history_responses):
                if idx >= history_img_start:
                    messages.append({
                        "role": "tool",
                        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tester.history_images[idx]}"}}],
                        "tool_call_id": "1"
                    })
                thinking, content_text = extract_thinking_and_content(response_text)
                messages.append({
                    "role": "assistant",
                    "content": content_text,
                    "reasoning_content": thinking,
                })
            # 当前截图
            messages.append({
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tester.history_images[-1]}"}}],
                "tool_call_id": "1"
            })
        else:
            # 首轮：只有当前截图
            messages.append({
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tester.history_images[-1]}"}}],
                "tool_call_id": "1"
            })
        
        return messages
    
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
        构建与 ExecutionRecorder 兼容的单轮 timing 字典。

        输入:
            round_num: 轮次序号
            round_start: 本轮开始时间戳
            think_start: 推理开始时间戳（即 API 调用开始）
            think_end: 推理结束时间戳
            action_end: 动作执行结束时间戳
            action_str: 动作描述字符串
            task: 任务描述
            screenshot_path: 截图文件路径
            response_text: 模型完整响应文本
            action_details: 结构化动作列表
        输出:
            timing 字典
        """
        sys_prompt = (
            SEED18_GUI_ONLY_SYSTEM_PROMPT
            if self.prompt_mode == "gui_only"
            else SEED18_TOOL_SYSTEM_PROMPT_ROLE
        )
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
                {"role": "system", "content": sys_prompt},
                {"role": "system", "content": "(tool definitions omitted for brevity)"},
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
        生成失败总结，帮助调试。
        
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
                actions_str = ", ".join(step.get("actions", []))
                summary_parts.append(f"  Round {step['round']}: {actions_str}")
        
        return "\n".join(summary_parts)
