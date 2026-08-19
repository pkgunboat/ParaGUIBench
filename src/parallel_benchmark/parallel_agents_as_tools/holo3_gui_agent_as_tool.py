"""Holo3 GUI Agent as Tool.

把 src.mm_agents.holo3.agent.Holo3Agent 封装成 BaseAgentTool 接口，供
Plan Agent / gui_only baseline 在 ParaGUIBench 各 pipeline 里调用。

设计差别（相对 Qwen / Kimi 的 as_tool 版本）:
- Holo3Agent 设计上是「无副作用单步预测器」：它只产出 `actions`，
  不会自己执行 pyautogui。本 tool 负责在每轮把 actions 喂给 controller
  执行，并把 sentinel（DONE / FAIL / WAIT / ANSWER:xxx）翻译成 status。
- 模型本地自托管（llama.cpp / vLLM GGUF），通过 OpenAI 兼容 API 调用。
  base_url / api_key / model 由 config.api_config 的 "holo3" provider 提供，
  支持 env 覆盖。
- token usage：llama.cpp 未必返回 usage，缺省全 0；如果以后挂上原生
  vLLM 服务能拿到，accumulator 已经预留好位置。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Dict, List

# 让 base_agent_tool 与 config 都能 import（与同目录其它 *_as_tool 保持一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 让 mm_agents.holo3.* 直接可达（src/ 加入 sys.path）。否则 ablation 入口下 sys.path
# 只有 src/parallel_benchmark/，无法解析 src.mm_agents 这种路径。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from .base_agent_tool import BaseAgentTool  # noqa: E402
from config.api_config import get_api_config, get_model_name  # noqa: E402


class Holo3GUIAgentTool(BaseAgentTool):
    """Holo3 GUI agent 工具封装。

    支持两种使用场景:
    - Plan Agent 模式：作为 GUI 子 Agent 被 Plan Agent 调用
    - gui_only 模式：作为独立 baseline 直接完成完整任务

    Holo3 内置 11 个工具 + reasoning，因此 prompt_mode 在这两种场景下当前
    无区分（参数保留是为了与 Qwen / Seed18 接口一致）。
    """

    def __init__(
        self,
        controller,
        model_name: str | None = None,
        prompt_mode: str = "gui_only",
        api_provider: str = "holo3",
        max_screenshots: int = 3,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 900.0,
        seed: int | None = 42,
    ):
        """初始化 Holo3 GUI agent tool。

        参数:
            controller:     PythonController 实例（VM 交互）。
            model_name:     模型名；None 时回退 get_model_name('holo3_gui_agent')。
            prompt_mode:    "gui_only" | "tool"；当前两种模式 Holo3 行为一致。
            api_provider:   API provider key（默认 "holo3" 指向本地 llama.cpp）。
            max_screenshots: 跨轮保留的截图数（Holo3 协议建议 3）。
            temperature:    模型温度。默认 0.0（贪心解码，保证 baseline 可复现）。
            max_tokens:     单次响应上限。512-1024 足够 Step JSON。
            timeout:        单次模型调用超时（秒）。GGUF 推理慢，给 15 分钟。
            seed:           固定 sampling 随机数发生器。默认 42 配合 temperature=0.0
                            保证 baseline 实验可复现；传 None 让 vllm 每次随机。
        """
        super().__init__(controller)
        self.model_name = model_name
        self.prompt_mode = prompt_mode
        self.api_provider = api_provider
        self.max_screenshots = max_screenshots
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.seed = seed

    # ------------------------------------------------------------------ public
    def execute(self, task: str, max_rounds: int = 15, timeout: int = 600) -> Dict:
        """执行 GUI 任务。

        参数:
            task:        任务文本。
            max_rounds:  最大轮次。
            timeout:     总时长上限（秒）。模型本身 self.timeout 是单次调用上限。

        返回:
            dict: status / result / steps / error / rounds_timing / model_name /
                  gui_token_usage
        """
        print(f"\n[Holo3GUIAgentTool] execute() task={task[:100]!r}")
        print(f"[Holo3GUIAgentTool] max_rounds={max_rounds} timeout={timeout}")

        start_time = time.time()
        steps: List[Dict] = []
        rounds_timing: List[Dict] = []
        thoughts: List[str] = []
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        model_name = self.model_name or get_model_name("holo3_gui_agent")
        api_cfg = get_api_config(self.api_provider)
        print(f"[Holo3GUIAgentTool] model={model_name} provider={self.api_provider} base={api_cfg['base_url']}")

        # 延迟导入：避免 import-time 触发 openai / pydantic 加载，影响纯工具调用
        try:
            from mm_agents.holo3.agent import Holo3Agent
        except Exception as e:  # noqa: BLE001
            return self._fail(
                steps, rounds_timing, model_name, token_usage,
                error=f"Failed to import Holo3Agent: {e}",
            )

        # 构造 agent。这里 enable_thinking 关掉（GGUF 服务多数会把 reasoning 漏到
        # content 里，对 Step 解析无害但占 token）。
        try:
            agent = Holo3Agent(
                base_url=api_cfg["base_url"],
                api_key=api_cfg["api_key"] or "EMPTY",
                model=model_name,
                platform="Ubuntu",
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                enable_thinking=False,
                timeout=self.timeout,
                max_screenshots=self.max_screenshots,
                seed=self.seed,
            )
            agent.reset()
        except Exception as e:  # noqa: BLE001
            return self._fail(
                steps, rounds_timing, model_name, token_usage,
                error=f"Failed to construct Holo3Agent: {e}",
            )

        # 反思总结需要一个 chat 客户端；复用同一组 API 配置（llama.cpp 本地服务
        # 一般支持 chat 端点）
        try:
            from openai import OpenAI as _OpenAI
            reflection_client = _OpenAI(
                api_key=api_cfg["api_key"] or "EMPTY",
                base_url=api_cfg["base_url"],
                timeout=self.timeout,
            )
        except Exception:  # noqa: BLE001
            reflection_client = None

        final_answer: str | None = None
        round_count = 0

        try:
            while round_count < max_rounds:
                # 总时长检查
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    summary = self._generate_failure_summary(
                        steps, thoughts, "timeout", round_count, max_rounds
                    )
                    return self._fail(
                        steps, rounds_timing, model_name, token_usage,
                        error=f"Timeout after {timeout}s. {summary}",
                    )

                round_count += 1
                round_start = time.time()
                print(f"\n{'='*60}\n[Holo3 GUI Agent] Round {round_count}/{max_rounds}\n{'='*60}")

                # 截图
                try:
                    screenshot = self.controller.get_screenshot()
                except Exception as e:  # noqa: BLE001
                    return self._fail(
                        steps, rounds_timing, model_name, token_usage,
                        error=f"Screenshot error: {e}",
                    )
                if screenshot is None:
                    return self._fail(
                        steps, rounds_timing, model_name, token_usage,
                        error="Screenshot capture failed",
                    )
                obs = {"screenshot": screenshot}

                # 模型推理
                try:
                    think_start = time.time()
                    result = agent.predict(task, obs)
                    think_end = time.time()
                except Exception as e:  # noqa: BLE001
                    error_trace = traceback.format_exc()
                    print(f"[ERROR] predict raised: {e}")
                    print(error_trace)
                    return self._fail(
                        steps, rounds_timing, model_name, token_usage,
                        error=f"Prediction error: {e}\n{error_trace}",
                    )

                tc = result.step.tool_call
                thought = result.step.thought or ""
                note = result.step.note or ""
                # llama.cpp 通常不返回 usage；如果服务端给了就累计
                raw_usage = getattr(result.raw.raw, "usage", None) if result.raw else None
                if raw_usage:
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        token_usage[k] += int(getattr(raw_usage, k, 0) or 0)

                thoughts.append(thought)
                print(f"[ACTION] tool={tc.tool_name} thought={thought[:160]!r}")
                print(f"[ACTIONS] {result.actions}")

                # 终态优先判定
                if result.actions and result.actions[0].startswith("ANSWER:"):
                    final_answer = result.actions[0][len("ANSWER:"):]
                    action_end = time.time()
                    rounds_timing.append(self._mk_timing(
                        round_count, round_start, think_start, think_end,
                        think_end, action_end, result, thought,
                    ))
                    steps.append(self._mk_step(round_count, thought, result.actions, action_end - start_time, note))
                    print(f"[SUCCESS] answered={final_answer!r}")
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        client=reflection_client, model_name=model_name,
                    )
                    return self.format_result(
                        success=True,
                        result=final_answer or reflection,
                        steps=steps,
                        rounds_timing=rounds_timing,
                        gui_token_usage=token_usage,
                    ) | {"model_name": model_name}

                if result.actions == ["DONE"]:
                    action_end = time.time()
                    rounds_timing.append(self._mk_timing(
                        round_count, round_start, think_start, think_end,
                        think_end, action_end, result, thought,
                    ))
                    steps.append(self._mk_step(round_count, thought, result.actions, action_end - start_time, note))
                    print(f"[SUCCESS] DONE in {round_count} rounds")
                    reflection = self._generate_reflection_summary(
                        task, steps, thoughts, "success",
                        client=reflection_client, model_name=model_name,
                    )
                    return self.format_result(
                        success=True,
                        result=reflection,
                        steps=steps,
                        rounds_timing=rounds_timing,
                        gui_token_usage=token_usage,
                    ) | {"model_name": model_name}

                if result.actions == ["FAIL"]:
                    action_end = time.time()
                    rounds_timing.append(self._mk_timing(
                        round_count, round_start, think_start, think_end,
                        think_end, action_end, result, thought,
                    ))
                    steps.append(self._mk_step(round_count, thought, result.actions, action_end - start_time, note))
                    summary = self._generate_failure_summary(
                        steps, thoughts, "execution_error", round_count, max_rounds
                    )
                    return self._fail(
                        steps, rounds_timing, model_name, token_usage,
                        error=f"Agent reports FAIL. {summary}",
                    )

                if result.actions == ["WAIT"]:
                    print("[INFO] WAIT requested, sleeping 2s")
                    action_end = time.time()
                    rounds_timing.append(self._mk_timing(
                        round_count, round_start, think_start, think_end,
                        think_end, action_end, result, thought,
                    ))
                    steps.append(self._mk_step(round_count, thought, result.actions, action_end - start_time, note))
                    time.sleep(2)
                    continue

                # 普通动作：在 VM 内执行 pyautogui
                action_start = time.time()
                for code in result.actions:
                    try:
                        self.controller.execute_python_command(code)
                    except Exception as e:  # noqa: BLE001
                        print(f"[WARN] action execution failed: {e}")
                action_end = time.time()

                rounds_timing.append(self._mk_timing(
                    round_count, round_start, think_start, think_end,
                    action_start, action_end, result, thought,
                ))
                steps.append(self._mk_step(round_count, thought, result.actions, action_end - start_time, note))

                # 留点时间让 UI 响应
                time.sleep(1.5)

            # max_rounds 用完
            reflection = self._generate_reflection_summary(
                task, steps, thoughts, "max_rounds",
                client=reflection_client, model_name=model_name,
            )
            return self._fail(
                steps, rounds_timing, model_name, token_usage,
                error=f"Reached maximum rounds ({max_rounds}) without completing.",
                result_text=reflection,
            )

        except Exception as e:  # noqa: BLE001
            error_trace = traceback.format_exc()
            print(f"[ERROR] unexpected: {e}")
            print(error_trace)
            return self._fail(
                steps, rounds_timing, model_name, token_usage,
                error=f"Unexpected error: {e}\n{error_trace}",
            )

    # ------------------------------------------------------------- private
    @staticmethod
    def _mk_step(round_num: int, thought: str, actions: List[str],
                 timestamp: float, note: str = "") -> Dict:
        return {
            "round": round_num,
            "thought": thought[:300] if thought else "",
            "actions": actions if isinstance(actions, list) else [str(actions)],
            "action": " | ".join(actions) if isinstance(actions, list) else str(actions),
            "timestamp": timestamp,
            "status": "executed",
            "output": (note + " " + thought)[:300].strip(),
        }

    @staticmethod
    def _mk_timing(round_num: int, round_start: float,
                   think_start: float, think_end: float,
                   action_start: float, action_end: float,
                   result, thought: str) -> Dict:
        return {
            "round": round_num,
            "duration": action_end - round_start,
            "think_start": think_start,
            "think_end": think_end,
            "action_start": action_start,
            "action_end": action_end,
            "action": " | ".join(result.actions) if isinstance(result.actions, list) else str(result.actions),
            "response_text": thought,
            "action_details": result.actions if isinstance(result.actions, list) else [],
            "messages": [],
            "screenshot_url": "",
            "timing": {
                "preparation": 0.0,
                "api_call": max(0.0, think_end - think_start),
                "parsing_and_execution": max(0.0, action_end - action_start),
            },
        }

    def _fail(
        self,
        steps: List[Dict],
        rounds_timing: List[Dict],
        model_name: str,
        token_usage: Dict,
        error: str,
        result_text: str = "",
    ) -> Dict:
        out = self.format_result(
            success=False,
            result=result_text,
            steps=steps,
            error=error,
            rounds_timing=rounds_timing,
            gui_token_usage=token_usage,
        )
        out["model_name"] = model_name
        return out

    def _generate_failure_summary(
        self,
        steps: List[Dict],
        thoughts: List[str],
        failure_reason: str,
        current_round: int,
        max_rounds: int,
    ) -> str:
        """轻量失败摘要（不调模型）。"""
        parts = [f"Completed {current_round}/{max_rounds} rounds before {failure_reason}."]
        if thoughts:
            parts.append("\nLast thoughts:")
            for i, t in enumerate(thoughts[-3:], 1):
                parts.append(f"  {i}. {t[:100]}...")
        if steps:
            parts.append("\nLast actions:")
            for s in steps[-3:]:
                act = ", ".join(s.get("actions", []))[:200]
                parts.append(f"  Round {s['round']}: {act}")
        return "\n".join(parts)
