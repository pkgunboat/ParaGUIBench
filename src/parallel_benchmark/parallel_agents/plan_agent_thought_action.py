"""
Plan Agent - Thought-Action Format (Experimental)
使用 Claude Sonnet 4.5 的 parallel tool calling 功能
保持与原 plan_agent.py 相同的核心策略，但改为 thought + action 模式
"""

import sys
import os
import json
import time
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.api_config import get_api_config
except ImportError:
    # 远程环境可能没有 config 模块；调用方应通过 api_key / base_url 参数传入
    def get_api_config(_provider="deerapi"):
        return {"api_key": "", "base_url": "https://api.deerapi.com/v1/"}

from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry
from desktop_env.controllers.python import PythonController
from prompts.plan_agent_prompt_thought_action import (
    MINIMAL_PARALLEL_PLANNER_PROMPT_THOUGHT_ACTION,
    get_plan_agent_prompt,
)
from dataviewer.execution_recorder import ExecutionRecorder


def _local_clean_answer(raw) -> str:
    """
    在不调用模型的前提下对 thought 中提取出的 <answer> 内容做轻量本地清洗。

    清洗规则：
        - 去首尾空白
        - 去外层同种引号（"" 或 ''）
        - 去括号补充说明（Malaysia (not Myanmar) → Malaysia）
        - 若仍含 <answer> tag，取第一个 tag 内容

    输入:
        raw: 原始字符串（Optional，None 视为空串）
    输出:
        str: 清洗后答案文本
    """
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    text = re.sub(r"\s*\([^)]*\)", "", text).strip()
    inner = re.findall(r"<answer>(.*?)</answer>", text, flags=re.DOTALL | re.IGNORECASE)
    if inner:
        text = inner[0].strip()
    return text


def _last_executed_round_all_failed(execution_log) -> bool:
    """
    查找 execution_log.rounds 中最近一个含 call_gui_agent 调用的轮次，
    判定该轮所有 call_gui_agent 结果 status 是否都 != "success"。

    P0-4 专用：thought 触发 <answer> 时当前轮无 tool_calls/results，
    拦截逻辑须回溯到上一个实际执行了 subagent 的轮次。

    兼容两种 results 结构：
      - round_log["results"] 简化版: {"tool_call_id", "function", "status", ...}
        (plan_agent_thought_action.py:711-719)
      - history[-1]["results"] 原始版: {"tool_call_id", "function",
        "result": {"status", "error", ...}} (plan_agent_thought_action.py:833)

    输入:
        execution_log: dict, 含 rounds 列表
    输出:
        bool: True 表示最近一个执行轮所有 subagent 失败
              False 表示：无执行轮 / 该轮至少一个成功
    """
    if not isinstance(execution_log, dict):
        return False
    rounds = execution_log.get("rounds") or []
    for r in reversed(rounds):  # 从后往前找最近执行轮
        if not isinstance(r, dict):
            continue
        tool_calls = r.get("tool_calls") or []
        has_gui_call = any(
            isinstance(tc, dict)
            and (tc.get("function") == "call_gui_agent"
                 or tc.get("name") == "call_gui_agent")
            for tc in tool_calls
        )
        if not has_gui_call:
            continue
        # 找到最近执行轮 → 检查 results
        results = r.get("results") or []
        gui_statuses = []
        for res in results:
            if not isinstance(res, dict):
                continue
            is_gui = (
                res.get("function") == "call_gui_agent"
                or res.get("name") == "call_gui_agent"
                or res.get("tool_name") == "call_gui_agent"
            )
            if not is_gui:
                continue
            # 简化版 status 顶层 vs 原始版 status 在 result 里
            status = res.get("status")
            if status is None:
                status = (res.get("result") or {}).get("status")
            gui_statuses.append(status)
        if not gui_statuses:
            # 有 tool_call 但无对应 result（异常结构），按未失败处理避免误伤
            return False
        return all(s != "success" for s in gui_statuses)
    return False  # 没找到任何已执行轮


def _maybe_override_with_insufficient_evidence(execution_log, thought_answer, task_logger):
    """
    若最近一轮 subagent 全挂，覆盖 thought_answer 为 INSUFFICIENT_EVIDENCE。

    输入:
        execution_log: dict，见 _last_executed_round_all_failed
        thought_answer: 原 thought 中提取并清洗后的答案
        task_logger: 日志对象（需支持 .warning）
    输出:
        str: 若触发拦截返回 "INSUFFICIENT_EVIDENCE"，否则返回原 thought_answer
    """
    if _last_executed_round_all_failed(execution_log):
        try:
            task_logger.warning(
                f"[ABSTAIN] All subagents failed in last round, "
                f"overriding thought answer '{thought_answer}' with INSUFFICIENT_EVIDENCE"
            )
        except Exception:
            pass
        return "INSUFFICIENT_EVIDENCE"
    return thought_answer


# ============================================================
# 模型定价表（单位: 美元 / 1M tokens，使用官方定价）
# 注：DeerAPI 代理的实际扣费可能与官方定价略有差异，
#     可通过 DeerAPI quota 差值交叉验证。
# ============================================================
MODEL_PRICING = {
    # OpenAI GPT-5
    "gpt-5-2025-08-07":            {"input": 2.50,  "output": 10.00},
    "gpt-5":                        {"input": 2.50,  "output": 10.00},
    # GPT-5.2（如无单独定价，这里暂按 gpt-5 计）
    "gpt-5.2":                      {"input": 2.50,  "output": 10.00},
    # Claude Sonnet 4.5
    "claude-sonnet-4-5-20250929":   {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-5":            {"input": 3.00,  "output": 15.00},
    # DeepSeek
    "deepseek-chat":                {"input": 0.27,  "output": 1.10},
    "deepseek-reasoner":            {"input": 0.55,  "output": 2.19},
    # Doubao Seed（火山引擎，活动期间价格，input ¥0.8/1M, output ¥2/1M，按 1USD≈7.2CNY 折算）
    "doubao-seed-1-8-251228":       {"input": 0.11,  "output": 0.28},
    # Doubao Seed 2.0 Pro（若无精确定价，这里暂置 0 以避免误导；需要时再补齐）
    "doubao-seed-2-0-pro-260215":   {"input": 0.0,   "output": 0.0},
}


def calculate_cost(token_usage: dict, model_name: str) -> dict:
    """
    根据 token 用量和模型定价计算费用

    Args:
        token_usage: {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        model_name: 模型名称（用于查找定价表）

    Returns:
        {"input_cost": float, "output_cost": float, "total_cost": float}
    """
    pricing = MODEL_PRICING.get(model_name, {"input": 0.0, "output": 0.0})
    prompt_tokens = token_usage.get("prompt_tokens", 0)
    completion_tokens = token_usage.get("completion_tokens", 0)

    input_cost = prompt_tokens * pricing["input"] / 1_000_000
    output_cost = completion_tokens * pricing["output"] / 1_000_000

    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost
    }


def print_token_usage_report(
    plan_agent_usage: dict,
    gui_agent_usage: dict,
    plan_agent_model: str,
    gui_agent_model: str,
    logger=None,
):
    """
    打印 Token 使用量和费用报告

    Args:
        plan_agent_usage: Plan Agent 的 token 用量字典
        gui_agent_usage: GUI Agent 的 token 用量字典
        plan_agent_model: Plan Agent 使用的模型名称
        gui_agent_model: GUI Agent 使用的模型名称
    """
    plan_cost = calculate_cost(plan_agent_usage, plan_agent_model)
    gui_cost = calculate_cost(gui_agent_usage, gui_agent_model)

    total_prompt = plan_agent_usage.get("prompt_tokens", 0) + gui_agent_usage.get("prompt_tokens", 0)
    total_completion = plan_agent_usage.get("completion_tokens", 0) + gui_agent_usage.get("completion_tokens", 0)
    total_tokens = total_prompt + total_completion
    total_cost = plan_cost["total_cost"] + gui_cost["total_cost"]

    report = f"""
{'='*63}
            Token Usage & Cost Report
{'='*63}
  Plan Agent ({plan_agent_model}):
    Input tokens:  {plan_agent_usage.get('prompt_tokens', 0):>8}    Cost: ${plan_cost['input_cost']:.4f}
    Output tokens: {plan_agent_usage.get('completion_tokens', 0):>8}    Cost: ${plan_cost['output_cost']:.4f}
    Subtotal:                           ${plan_cost['total_cost']:.4f}

  GUI Agent ({gui_agent_model}):
    Input tokens:  {gui_agent_usage.get('prompt_tokens', 0):>8}    Cost: ${gui_cost['input_cost']:.4f}
    Output tokens: {gui_agent_usage.get('completion_tokens', 0):>8}    Cost: ${gui_cost['output_cost']:.4f}
    Subtotal:                           ${gui_cost['total_cost']:.4f}
{chr(8722)*63}
  Total tokens: {total_tokens:>8}       Total Cost: ${total_cost:.4f}
{'='*63}"""

    if logger:
        for line in report.strip().split('\n'):
            logger.info(line)
    else:
        print(report)


class PlanAgentThoughtAction:
    """
    Plan Agent - 使用 Thought-Action 格式
    
    与原版 plan_agent.py 的主要区别：
    1. 不生成 JSON 计划，而是输出 thought（思考过程）
    2. 使用 Claude Sonnet 4.5 的 parallel tool calling 来执行 action
    3. 保持相同的 Agent Selection Strategy 和 Parallelization Strategy
    """

    def __init__(
        self,
        controller: PythonController,
        registry: AgentToolRegistry,
        max_workers: int = 4,
        vm_controllers: Optional[List[PythonController]] = None,
        api_key: str = None,
        base_url: str = None,
        disable_code_agent: bool = False,  # 兼容保留参数（已停用 Code Agent）
        coordinator_model: str = "gpt-5-2025-08-07",  # Coordinator使用的模型名称
        gui_step_budget: Optional[int] = 200,  # 全局 GUI 步数预算（默认 200 步）
        num_agents: Optional[int] = None,  # GUI Agent 数量（默认取 vm_controllers 长度）
        task_logger: Optional[Any] = None,
        progress_state: Optional[Any] = None,
        thread_name: str = "",
    ) -> None:
        self.controller = controller
        self.registry = registry
        self.coordinator_model = coordinator_model  # 保存模型名称

        # 全局 GUI 步数预算
        self.gui_step_budget = gui_step_budget
        self._gui_steps_used = 0  # 已消耗的 GUI 步数

        # 使用 deerapi 代理调用 GPT-5
        import os
        _api_config = get_api_config("deerapi")
        self.vlm = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", _api_config["api_key"]),
            base_url=base_url or _api_config["base_url"],
        )
        self.max_workers = max_workers

        # 支持多虚拟机
        self.vm_controllers = vm_controllers or [controller]
        self.next_gui_vm_index = 0  # fallback 轮询分配（LLM 未传 agent_id 时使用）

        # GUI Agent 数量：优先使用显式指定值，否则取 vm_controllers 长度
        self.num_agents = num_agents if num_agents is not None else len(self.vm_controllers)

        # 根据 num_agents 生成动态 system prompt
        self.system_prompt = get_plan_agent_prompt(self.num_agents)

        # 日志记录（用于 trajectory 可视化）
        self.execution_log = None

        # 初始化 ExecutionRecorder（用于精确时间记录）
        self.recorder = None

        # Token usage 追踪（Plan Agent 自身的 API 调用）
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # GUI Agent 累计 token usage（从各次 GUI Agent 调用结果中汇总）
        self.gui_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # GUI Agent 使用的模型名称（从首个 GUI Agent 结果中提取）
        self.gui_agent_model = ""

        # Per-task logger（由 pipeline_base 注入，用于写入独立 .log 文件）
        # 如果未传入，创建 fallback logger 输出到 stdout（向后兼容）
        import logging as _logging
        if task_logger is not None:
            self.task_logger = task_logger
        else:
            self.task_logger = _logging.getLogger(f"plan_agent.{id(self)}")
            if not self.task_logger.handlers:
                self.task_logger.addHandler(_logging.StreamHandler(sys.stdout))
                self.task_logger.setLevel(_logging.INFO)
                self.task_logger.propagate = False

        # ProgressState 引用（可选，仪表板更新用）
        self._progress_state = progress_state
        self._thread_name = thread_name

        # 定义可用的工具（GUI-only，支持 agent_id 显式指定 Agent，enum 根据 num_agents 动态生成）
        agent_id_enum = list(range(1, self.num_agents + 1))
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "call_gui_agent",
                    "description": (
                        "Dispatch a task to a specific GUI Agent. "
                        f"You have {self.num_agents} GUI Agents (agent_id: 1-{self.num_agents}), "
                        "each running on its own isolated environment "
                        "with independent browser session, cookies, cart, and login state. "
                        "The VM does NOT restart between rounds — all state is preserved. "
                        "Use the same agent_id across rounds to maintain session continuity. "
                        "IMPORTANT: When opening files, instruct the agent to use double_click action. "
                        "For information gathering, the agent should RETURN data directly, NOT save to files."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_description": {
                                "type": "string",
                                "description": "Clear description of the GUI task."
                            },
                            "agent_id": {
                                "type": "integer",
                                "description": (
                                    f"Which GUI Agent to dispatch this task to (1-{self.num_agents}). "
                                    "Each agent has its own isolated browser/desktop. "
                                    "Use the SAME agent_id across rounds for tasks that need session continuity "
                                    "(e.g., add to cart then checkout must use the same agent)."
                                ),
                                "enum": agent_id_enum
                            }
                        },
                        "required": ["task_description", "agent_id"]
                    }
                }
            }
        ]

    def _print_cost_report(self):
        """
        打印本次执行的 Token 费用报告。

        自动从 self.token_usage（Plan Agent）和 self.gui_token_usage（GUI Agent）
        中读取累计数据，并根据 MODEL_PRICING 计算费用。
        """
        # 推断 GUI Agent 使用的模型名称
        # 优先级对齐 AgentToolRegistry: gpt54 > seed18 > kimi > doubao > qwen > gpt > claude
        # 优先使用 self.gui_agent_model（从实际 tool.execute 返回的 model_name 填充）
        gui_model = getattr(self, "gui_agent_model", "") or "claude-sonnet-4-5-20250929"
        try:
            use_gpt54 = getattr(self.registry, 'use_gpt54_gui', False)
            use_seed18 = getattr(self.registry, 'use_seed18_gui', False)
            use_qwen = getattr(self.registry, 'use_qwen_gui', False)
            use_gpt = getattr(self.registry, 'use_gpt_gui', False)
            use_kimi = getattr(self.registry, 'use_kimi_gui', False)
            use_doubao = getattr(self.registry, 'use_doubao_gui', False)
            if use_gpt54:
                gui_model = "gpt-5.4-mini"
            elif use_seed18:
                gui_model = "doubao-seed-1-8-251228"
            elif use_qwen:
                gui_model = "qwen-vl-max"
            elif use_gpt:
                gui_model = "gpt-5"
            elif use_kimi:
                gui_model = "kimi"
            elif use_doubao:
                gui_model = "doubao"
        except Exception:
            pass

        print_token_usage_report(
            plan_agent_usage=self.token_usage,
            gui_agent_usage=self.gui_token_usage,
            plan_agent_model=self.coordinator_model,
            gui_agent_model=gui_model,
            logger=self.task_logger,
        )

    def execute_task(
        self,
        task: str,
        context: Optional[str] = None,
        max_rounds: int = 10,
        max_rounds_per_subtask: int = 50,
        timeout_per_subtask: int = 0,
        task_timeout: int = 7200,
    ) -> Dict[str, Any]:
        """
        使用 thought-action 模式执行任务

        Args:
            task: 任务描述
            context: 可选的上下文信息
            max_rounds: 最多执行多少轮 thought-action（防止无限循环）
            max_rounds_per_subtask: 每个子任务的最大执行轮次
            timeout_per_subtask: 每个子任务的超时时间（秒，0 表示不限制）
            task_timeout: 整体任务超时时间（秒，默认 7200 即 2 小时，0 表示不限制）

        Returns:
            执行结果字典
        """
        start_time = time.time()
        start_timestamp = time.time()

        # 保存原始 instruction，供 GUI Agent 子任务追加全局上下文
        self.task_instruction = task

        # 重置全局 GUI 步数计数
        self._gui_steps_used = 0
        
        # 初始化执行日志（用于 trajectory 可视化）
        self.execution_log = {
            "task": task,
            "start_time": datetime.now().isoformat(),
            "start_timestamp": start_timestamp,
            "rounds": []
        }
        
        # 初始化 ExecutionRecorder（用于精确时间记录 v2.0 格式）
        # 使用实际的coordinator model名称和system prompt
        self.recorder = ExecutionRecorder(
            instruction=task,
            coordinator_model=self.coordinator_model,  # 使用实际的模型名称
            coordinator_system_prompt=self.system_prompt  # 使用动态生成的 prompt
        )
        self.recorder.start_task()
        
        history: List[Dict[str, Any]] = []
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._build_user_prompt(task, context)}
        ]
        
        # 用于跟踪连续无工具调用的轮次
        consecutive_no_tool_calls = 0
        # 用于跟踪连续 API 致命错误的轮次（如 GUI Agent API 欠费 403）
        self._consecutive_api_fatal_rounds = 0
        
        for round_num in range(max_rounds):
            # === Fix 3: 整体任务超时检查 ===
            if task_timeout > 0:
                wall_elapsed = time.time() - start_time
                if wall_elapsed >= task_timeout:
                    self.task_logger.warning(f"[TIMEOUT] 整体任务超时: 已运行 {wall_elapsed:.0f}s >= 上限 {task_timeout}s，强制结束")
                    self.task_logger.warning(f"[TIMEOUT] 已完成 {round_num}/{max_rounds} 轮，GUI 步数: {self._gui_steps_used}/{self.gui_step_budget}")
                    break

            self.task_logger.info("=" * 60)
            self.task_logger.info(f"Round {round_num + 1}/{max_rounds}")
            self.task_logger.info("=" * 60)

            if self._progress_state and self._thread_name:
                self._progress_state.update_thread(
                    self._thread_name, self.task_instruction[:50],
                    f"Round {round_num+1}/{max_rounds} preparing",
                    time.time() - start_time
                )

            # 创建轮次日志
            round_timestamp = time.time()
            round_log = {
                "round": round_num + 1,
                "timestamp": round_timestamp,
                "relative_time": round_timestamp - start_timestamp,
                "thought": None,
                "tool_calls": [],
                "results": [],
                "timing": {}  # 添加时间记录
            }
            
            # ========== 阶段1: Round开始到API调用（准备阶段）==========
            preparation_start_time = time.time()
            # 这里可能包括截图、状态检查等准备工作
            # 目前主要是构建消息的时间
            preparation_end_time = time.time()
            round_log["timing"]["preparation_time"] = preparation_end_time - preparation_start_time
            self.task_logger.info(f"[TIMING] Preparation: {round_log['timing']['preparation_time']:.3f}s")

            if self._progress_state and self._thread_name:
                self._progress_state.update_thread(
                    self._thread_name, self.task_instruction[:50],
                    f"R{round_num+1} API call...",
                    time.time() - start_time
                )

            # ========== 阶段2: API调用时间 ==========
            model_start_time = time.time()
            try:
                from parallel_benchmark.utils.llm_determinism import (
                    LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
                )
                _plan_kwargs = dict(
                    model=self.coordinator_model,
                    messages=messages,
                    tools=self.tools,
                    temperature=LLM_TEMPERATURE,
                    seed=LLM_SEED,
                    max_tokens=8000,  # 增大 token 限制，避免被截断
                    parallel_tool_calls=True,  # 启用并行工具调用
                )
                assert_deterministic(_plan_kwargs)
                response = self.vlm.chat.completions.create(**_plan_kwargs)
                model_end_time = time.time()
                api_call_time = model_end_time - model_start_time
                round_log["timing"]["api_call_time"] = api_call_time
                self.task_logger.info(f"[TIMING] API Call: {api_call_time:.3f}s")
            except Exception as e:
                self.task_logger.error(f"Error calling LLM: {e}")
                round_log["error"] = str(e)
                self.execution_log["rounds"].append(round_log)
                
                # 完成 recorder 记录
                self.recorder.finish_task(success=False)
                try:
                    # 保存到 logs/ 目录
                    current_dir = os.path.dirname(os.path.abspath(__file__))
                    parallel_benchmark_dir = os.path.dirname(current_dir)
                    logs_dir = os.path.join(parallel_benchmark_dir, "logs")
                    os.makedirs(logs_dir, exist_ok=True)
                    record_path = os.path.join(logs_dir, "execution_record.json")
                    self.recorder.save_to_file(record_path)
                except:
                    pass
                
                # 打印费用报告（即使 API 调用出错也输出已消耗的 token 情况）
                self._print_cost_report()
                
                return {
                    "success": False,
                    "error": str(e),
                    "history": history,
                    "elapsed_time": time.time() - start_time,
                    "execution_log": self.execution_log,
                    "execution_record": self.recorder.get_record(),
                    "token_usage": {
                        "plan_agent": dict(self.token_usage),
                        "gui_agent": dict(self.gui_token_usage),
                        "plan_agent_model": self.coordinator_model,
                        "gui_agent_model": self.gui_agent_model,
                    }
                }

            # 累计 Plan Agent 自身的 token usage
            if hasattr(response, 'usage') and response.usage is not None:
                self.token_usage["prompt_tokens"] += getattr(response.usage, 'prompt_tokens', 0) or 0
                self.token_usage["completion_tokens"] += getattr(response.usage, 'completion_tokens', 0) or 0
                self.token_usage["total_tokens"] += getattr(response.usage, 'total_tokens', 0) or 0
                # 将本轮 token 用量写入 round_log（补全缺失的逐轮记录）
                round_log["token_usage"] = {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) or 0,
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                    "total_tokens": getattr(response.usage, 'total_tokens', 0) or 0,
                    "cumulative_total": self.token_usage["total_tokens"],
                }
                self.task_logger.info(f"[TOKEN] Plan Agent this round: prompt={getattr(response.usage, 'prompt_tokens', 0)}, completion={getattr(response.usage, 'completion_tokens', 0)}, cumulative_total={self.token_usage['total_tokens']}")
            
            message = response.choices[0].message
            
            # 调试：打印完整的 message 对象
            self.task_logger.debug(f"API Response message:")
            self.task_logger.debug(f"  - content: {message.content[:200] if message.content else 'None'}...")
            self.task_logger.debug(f"  - tool_calls: {message.tool_calls}")
            self.task_logger.debug(f"  - finish_reason: {response.choices[0].finish_reason}")
            
            # 记录 thought 内容
            if message.content:
                round_log["thought"] = message.content
                self.task_logger.info(f"[THOUGHT]\n{message.content}")

            # 检测 <answer> 标签：如果 LLM 在 thought 中给出了最终答案，立即终止循环
            # 防止 Plan Agent 在已得出答案后继续发起不必要的验证轮次
            if message.content:
                _answer_match = re.search(r"<answer>(.*?)</answer>", message.content, re.DOTALL | re.IGNORECASE)
                if _answer_match:
                    _raw_answer = _answer_match.group(1).strip()
                    # P0-2: 本地清洗，避免二次模型调用修改答案内容
                    _extracted_answer = _local_clean_answer(_raw_answer)
                    # P0-4: 若之前最近一轮 call_gui_agent 全部失败，覆盖为 INSUFFICIENT_EVIDENCE
                    _extracted_answer = _maybe_override_with_insufficient_evidence(
                        self.execution_log, _extracted_answer, self.task_logger
                    )
                    self.task_logger.info(
                        f"[ANSWER DETECTED] Found <answer> tag in thought: "
                        f"raw='{_raw_answer}' cleaned='{_extracted_answer}'. Terminating loop."
                    )
                    self.recorder.set_final_answer(_extracted_answer)
                    self.execution_log["rounds"].append(round_log)
                    break

            # 检查是否有工具调用
            if not message.tool_calls:
                self.task_logger.info(f"No structured tool calls, attempting to parse XML from content...")
                
                # 尝试从 content 里解析 XML 格式的工具调用
                # 有时 deerapi 会返回 XML 文本而不是结构化的 tool_calls
                xml_tool_calls = None
                if message.content and ("<function_calls>" in message.content or "<invoke" in message.content):
                    self.task_logger.debug(f"Content length: {len(message.content)} chars")
                    self.task_logger.debug(f"Full content:\n{message.content}")
                    self.task_logger.debug(f"Content contains <function_calls>: {'<function_calls>' in message.content}")
                    self.task_logger.debug(f"Content contains <invoke: {'<invoke' in message.content}")
                    
                    try:
                        import xml.etree.ElementTree as ET
                        # 注意：re 使用文件顶部的全局 import，不要在此处局部导入

                        # 提取所有 <invoke> 块
                        invoke_pattern = r'<invoke\s+name="([^"]+)">\s*<parameter\s+name="([^"]+)">([^<]+)</parameter>\s*</invoke>'
                        matches = re.findall(invoke_pattern, message.content, re.DOTALL)
                        
                        self.task_logger.debug(f"Regex matches found: {len(matches)}")
                        if matches:
                            self.task_logger.debug(f"Matches: {matches}")
                        
                        if matches:
                            # 构造伪 tool_calls 对象
                            _xml_call_counter = [0]  # 用列表避免闭包问题
                            class FakeToolCall:
                                def __init__(self, func_name, args_dict):
                                    _xml_call_counter[0] += 1
                                    self.id = f"xml_{func_name}_{_xml_call_counter[0]}_{id(args_dict)}"
                                    self.type = "function"
                                    self.function = type('obj', (object,), {
                                        'name': func_name,
                                        'arguments': json.dumps(args_dict)
                                    })()
                            
                            xml_tool_calls = []
                            for func_name, param_name, param_value in matches:
                                args = {param_name: param_value.strip()}
                                # XML fallback 中 LLM 可能遗漏 agent_id，自动补充
                                if func_name == "call_gui_agent" and "agent_id" not in args:
                                    fallback_id = (self.next_gui_vm_index % len(self.vm_controllers)) + 1
                                    self.next_gui_vm_index = (self.next_gui_vm_index + 1) % len(self.vm_controllers)
                                    args["agent_id"] = fallback_id
                                    self.task_logger.warning(f"XML fallback: agent_id missing, auto-assigned agent_id={fallback_id}")
                                xml_tool_calls.append(FakeToolCall(func_name, args))

                            self.task_logger.info(f"Parsed {len(xml_tool_calls)} tool calls from XML format")
                            for tc in xml_tool_calls:
                                self.task_logger.debug(f"  - {tc.function.name}({tc.function.arguments})")
                            # 使用解析出的工具调用
                            message.tool_calls = xml_tool_calls
                        else:
                            self.task_logger.debug("XML parsing failed - no valid <invoke> blocks found")
                            self.task_logger.debug("Expected pattern: <invoke name=\"...\"><parameter name=\"...\">...</parameter></invoke>")
                    except Exception as e:
                        self.task_logger.error(f"Failed to parse XML: {e}")
                        import traceback
                        self.task_logger.error(traceback.format_exc())
                
                # 如果解析成功，message.tool_calls 已经被设置，跳过下面的逻辑
                # 如果还是没有工具调用
                if not message.tool_calls:
                    self.task_logger.info(f"No tool calls - task may be complete or LLM didn't call tools")
                    # 将助手消息添加到历史
                    messages.append(message.model_dump())
                    
                    # 记录此轮
                    self.execution_log["rounds"].append(round_log)

                    # 记录此轮到 recorder (即使没有工具调用也要记录，特别是最后一轮)
                    self.recorder.add_plan_agent_round(
                        round_num=round_num,
                        model_start_time=model_start_time,
                        model_end_time=model_end_time,
                        response=message.content or "",
                        thought={
                            "analysis": message.content[:500] if message.content else "",
                            "tool_calls": 0
                        },
                        action_start_time=time.time(),
                        action_end_time=time.time(),
                        dispatched_agents=[],
                        messages=messages.copy(),
                        dependencies={}
                    )
                    
                    # 累计连续无工具调用的轮次
                    consecutive_no_tool_calls += 1
                    
                    # 如果是第一轮没有工具调用，给 LLM 一个提示
                    if consecutive_no_tool_calls == 1 and round_num == 0:
                        self.task_logger.info(f"First round without tool calls - prompting LLM to use tools")
                        messages.append({
                            "role": "user",
                            "content": (
                                "You did NOT make any tool calls in your last response. "
                                "You MUST use the function-calling API to invoke call_gui_agent — "
                                "do NOT write tool calls as plain text. "
                                "Please re-analyze the task and issue a structured tool call now."
                            )
                        })
                        continue
                    
                    # 如果上一轮有工具调用且全部成功，这一轮没有工具调用
                    # 检查任务是否真正完成
                    if consecutive_no_tool_calls == 1 and len(history) > 0:
                        last_round = history[-1]
                        all_success = all(r.get("result", {}).get("status") == "success" for r in last_round.get("results", []))
                        
                        # 检查 LLM 是否明确表示完成
                        if all_success and round_log.get("thought"):
                            thought_lower = round_log["thought"].lower()
                            completion_keywords = [
                                "task completed", "task is complete", "all tasks completed",
                                "finished", "done", "successfully completed",
                                "all parts are complete", "all parts complete",
                                "all steps are complete", "all steps complete",
                                "no further actions required", "no further actions needed",
                                "everything is complete", "task complete"
                            ]
                            if any(keyword in thought_lower for keyword in completion_keywords):
                                self.task_logger.info(f"Previous round succeeded and LLM confirmed completion - task completed")
                                # 注意：这里的round_log已经在上面被记录到recorder了，无需重复记录
                                # Extract and set final answer
                                final_answer = round_log.get("thought", "")
                                if final_answer:
                                    self.recorder.set_final_answer(final_answer)
                                break
                            else:
                                self.task_logger.info(f"Previous round succeeded but LLM didn't confirm completion - continuing")
                                messages.append({
                                    "role": "user",
                                    "content": "Review the original task and completed steps. If all parts are done, confirm completion. If steps remain (e.g., writing results to a file), call the appropriate tool to finish them."
                                })
                                continue
                    
                    # 如果连续没有工具调用但还是首轮（round_num < 3），再提示一次
                    if consecutive_no_tool_calls == 2 and round_num < 3:
                        self.task_logger.info(f"Second round without tool calls - giving one more strong prompt")
                        messages.append({
                            "role": "user",
                            "content": (
                                "REMINDER: You must issue a structured tool call via the API, not as text. "
                                "Call call_gui_agent with a task_description string now."
                            )
                        })
                        continue
                    
                    # 如果连续三轮没有工具调用，认为任务完成
                    if consecutive_no_tool_calls >= 3:
                        self.task_logger.info(f"Three consecutive rounds without tool calls - ending execution")
                        # 注意：这里的round_log已经在上面被记录到recorder了，无需重复记录
                        # Extract and set final answer
                        final_answer = round_log.get("thought", "")
                        if final_answer:
                            self.recorder.set_final_answer(final_answer)
                        break
                    continue
            
            # 有工具调用，重置计数器
            consecutive_no_tool_calls = 0
            
            # 记录助手消息（包含 tool_calls）
            messages.append(message.model_dump())
            
            # 计算本轮 GUI Agent 可用步数（考虑并行 Agent 数量）
            if self.gui_step_budget is not None:
                remaining_budget = self.gui_step_budget - self._gui_steps_used
                if remaining_budget <= 0:
                    self.task_logger.info(f"[BUDGET] GUI step budget exhausted ({self._gui_steps_used}/{self.gui_step_budget}). Skipping tool calls.")
                    # 构造虚拟结果告知 LLM 步数已用完
                    tool_results = []
                    for tc in message.tool_calls:
                        tool_results.append({
                            "tool_call_id": tc.id,
                            "function": tc.function.name,
                            "arguments": self._load_tool_arguments(tc),
                            "result": {
                                "status": "failure",
                                "result": "",
                                "steps": [],
                                "error": f"GUI step budget exhausted ({self._gui_steps_used}/{self.gui_step_budget} steps used). No more actions allowed."
                            },
                            "agent_count": f"budget_exhausted_{tc.id}"
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"GUI step budget exhausted ({self._gui_steps_used}/{self.gui_step_budget} steps used). Please summarize results and finish."
                        })
                    self.execution_log["rounds"].append(round_log)
                    break
                effective_max_rounds = min(max_rounds_per_subtask, remaining_budget)
                self.task_logger.info(f"[BUDGET] GUI steps: {self._gui_steps_used}/{self.gui_step_budget} used, {remaining_budget} remaining. This round max: {effective_max_rounds}")
            else:
                effective_max_rounds = max_rounds_per_subtask
            
            # 执行工具调用（可能是并行的）
            self.task_logger.info(f"[ACTIONS] Executing {len(message.tool_calls)} tool call(s)...")

            if self._progress_state and self._thread_name:
                n_tools = len(message.tool_calls)
                self._progress_state.update_thread(
                    self._thread_name, self.task_instruction[:50],
                    f"R{round_num+1} GUI×{n_tools} exec...",
                    time.time() - start_time
                )

            tool_results = self._execute_tool_calls(
                message.tool_calls,
                effective_max_rounds,
                timeout_per_subtask,
                round_log  # 传递 round_log 用于记录
            )

            # 主线程统一填充 round_log（替代原先工作线程中的并发写入）
            round_log["tool_calls"] = [
                {
                    "id": tc.id,
                    "function": tc.function.name,
                    "arguments": self._load_tool_arguments(tc),
                }
                for tc in message.tool_calls
            ]
            round_log["results"] = [
                {
                    "tool_call_id": tr.get("tool_call_id", ""),
                    "function": tr.get("function", ""),
                    "status": tr.get("result", {}).get("status", "unknown") if isinstance(tr.get("result"), dict) else "unknown",
                    "agent_count": tr.get("agent_count", ""),
                }
                for tr in tool_results
            ]

            # ========== GUI Agent API 致命错误检测 ==========
            # 检测所有 GUI Agent 是否都因 API 级别错误（如欠费 403）而失败
            # 这类错误无法通过重试恢复，继续执行只会浪费 Plan Agent token
            _API_FATAL_KEYWORDS = [
                "AccountOverdueError", "AccountOverdue",
                "403", "401", "Unauthorized",
                "quota exceeded", "rate limit",
                "billing", "payment required",
            ]
            if tool_results:
                api_fatal_count = 0
                for tr in tool_results:
                    tr_result = tr.get("result", {})
                    tr_error = str(tr_result.get("error", ""))
                    tr_status = tr_result.get("status", "")
                    if tr_status == "failure" and any(kw.lower() in tr_error.lower() for kw in _API_FATAL_KEYWORDS):
                        api_fatal_count += 1

                if api_fatal_count == len(tool_results):
                    # 本轮所有 GUI Agent 都因 API 错误失败
                    if not hasattr(self, '_consecutive_api_fatal_rounds'):
                        self._consecutive_api_fatal_rounds = 0
                    self._consecutive_api_fatal_rounds += 1
                    self.task_logger.error(f"[API FATAL] 本轮所有 {api_fatal_count} 个 GUI Agent 均因 API 错误失败 "
                          f"(连续 {self._consecutive_api_fatal_rounds} 轮)")

                    if self._consecutive_api_fatal_rounds >= 2:
                        sample_error = tool_results[0].get("result", {}).get("error", "")[:200]
                        self.task_logger.error(f"[API FATAL] 连续 2 轮 API 错误，判定为不可恢复的 API 故障，终止任务执行")
                        self.task_logger.error(f"[API FATAL] 错误信息: {sample_error}")

                        # 记录到日志
                        self.execution_log["rounds"].append(round_log)
                        self.recorder.set_final_answer(
                            f"[ABORTED] GUI Agent API fatal error after {round_num + 1} rounds. "
                            f"Error: {sample_error}"
                        )
                        self.recorder.finish_task(success=False)
                        self._print_cost_report()
                        return {
                            "final_answer": "",
                            "execution_log": self.execution_log,
                            "total_rounds": round_num + 1,
                            "status": "api_fatal_error",
                            "error": sample_error,
                        }
                else:
                    # 本轮有正常工作的 GUI Agent，重置计数器
                    self._consecutive_api_fatal_rounds = 0

            # 更新全局 GUI 步骤计数（每轮模型调用 = 1 个步骤）
            if self.gui_step_budget is not None:
                for tr in tool_results:
                    steps = tr.get("result", {}).get("steps", [])
                    self._gui_steps_used += len(steps)
                self.task_logger.info(f"[BUDGET] After this round: {self._gui_steps_used}/{self.gui_step_budget} GUI steps used")
            
            # ========== 依赖关系分析（从 LLM 输出的 XML 中解析）==========
            self.task_logger.info(f"[DEPENDENCY ANALYSIS] Parsing dependencies from LLM output...")
            
            # 从 tool_results 中提取 agent_count 映射
            tool_agent_count_map = {}
            for tool_result in tool_results:
                tc_id = tool_result.get("tool_call_id")
                agent_count = tool_result.get("agent_count")  # 从结果中获取 agent_count
                if tc_id and agent_count:
                    tool_agent_count_map[tc_id] = agent_count
            
            # 尝试从 message.content 中解析 XML 格式的依赖关系
            dependencies = self._parse_dependencies_from_xml(
                message.content,
                tool_agent_count_map,
                round_num + 1
            )
            
            # 如果 XML 解析失败，使用启发式分析作为后备
            if not dependencies:
                self.task_logger.warning("⚠️  No XML dependencies found, falling back to heuristic analysis")
                dependencies = self._analyze_dependencies(
                    message.tool_calls,
                    history,
                    message.content,
                    tool_agent_count_map
                )
            
            # 打印依赖关系
            for agent_id, dep_info in dependencies.items():
                deps = dep_info.get("depends_on", [])
                task = dep_info.get("task", "")[:60]
                if deps:
                    deps_str = ", ".join([d["agent_id"] for d in deps])
                    self.task_logger.info(f"📍 {agent_id} ({task}...)")
                    self.task_logger.info(f"  ⬅️  depends on: {deps_str}")
                else:
                    self.task_logger.info(f"🆕 {agent_id} ({task}...) - No dependencies")
            
            # 将依赖关系记录到 round_log
            round_log["dependencies"] = dependencies
            
            # 记录本轮结果
            round_record = {
                "round": round_num + 1,
                "thought": message.content,
                "dependencies": dependencies,  # 添加依赖关系记录
                "tool_calls": [
                    {
                        "id": tc.id,
                        "function": tc.function.name,
                        "arguments": self._load_tool_arguments(tc)
                    }
                    for tc in message.tool_calls
                ],
                "results": tool_results
            }
            history.append(round_record)
            
            # 将工具结果添加到消息历史
            for tool_result in tool_results:
                # 格式化工具结果，让 LLM 更容易理解
                result_data = tool_result.get("result")
                if not isinstance(result_data, dict):
                    self.task_logger.warning(f"Unexpected result type: {type(result_data).__name__}, wrapping as failure")
                    result_data = {"status": "failure", "result": "", "steps": [],
                                   "error": f"Unexpected result format: {type(result_data).__name__}"}

                # 构建清晰的结果描述
                if result_data.get("status") == "success":
                    # 提取关键信息：主要结果内容
                    main_result = result_data.get("result", "")
                    
                    # 如果有步骤信息，提取最后一步的输出（通常包含实际数据）
                    steps = result_data.get("steps", [])
                    actual_output = ""
                    if steps and len(steps) > 0 and isinstance(steps, list):
                        # 查找最后一个有实质内容的步骤输出
                        # 兼容 List[Dict] 和 List[str] 两种格式：
                        #   - Dict 格式（Claude GUI Agent）: {"status": "success", "output": "..."}
                        #   - str 格式（GPT/Kimi 等 GUI Agent）: "Round 1: click(100, 200)"
                        for step in reversed(steps):
                            output_text = ""
                            if isinstance(step, dict):
                                step_status = step.get("status", "")
                                if step_status in ("success", "executed") and step.get("output"):
                                    output_text = step["output"]
                            elif isinstance(step, str) and len(step) > 20:
                                output_text = step

                            if output_text and len(output_text) > 50:
                                actual_output = f"\n\nActual Output:\n{output_text}"
                                break

                        steps_summary = f"\nExecution steps: {len(steps)} steps completed"
                    else:
                        steps_summary = ""
                    
                    # 从 tool_result 中获取 agent_id，标注结果来源
                    result_agent_id = tool_result.get("arguments", {}).get("agent_id", "?")

                    formatted_content = (
                        f"✓ SUCCESS [Agent {result_agent_id}]\n"
                        f"Task: {tool_result.get('arguments', {}).get('task_description', '')[:100]}\n\n"
                        f"Result:\n{main_result}{steps_summary}{actual_output}"
                    )
                else:
                    # 失败情况 - 包含详细的失败原因
                    error = result_data.get("error", "Unknown error")
                    result_msg = result_data.get("result", "")
                    result_agent_id = tool_result.get("arguments", {}).get("agent_id", "?")

                    # 构建详细的失败描述
                    failure_details = []
                    failure_details.append(f"✗ FAILURE [Agent {result_agent_id}]")
                    failure_details.append(f"Task: {tool_result.get('arguments', {}).get('task_description', '')[:150]}")
                    failure_details.append(f"\nError: {error}")

                    # 如果有额外的结果信息（包含失败总结）
                    if result_msg and result_msg != error:
                        failure_details.append(f"\nDetails:\n{result_msg[:500]}")

                    # 兜底：从 steps 中提取 text_only 回答，避免 Agent 已找到答案但因 failure 状态丢失
                    steps = result_data.get("steps", [])
                    if steps and isinstance(steps, list):
                        for step in reversed(steps):
                            if isinstance(step, dict) and step.get("status") == "text_only" and step.get("output"):
                                text_answer = step["output"]
                                if len(text_answer.strip()) > 20:
                                    failure_details.append(f"\nAgent's text answer (from text_only round):\n{text_answer[:800]}")
                                    break

                    formatted_content = "\n".join(failure_details)
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": formatted_content
                })
                
                # 调试输出
                self.task_logger.info(f"[TOOL RESULT] Sending to LLM:\n{formatted_content[:300]}...")
            
            # 添加摘要消息，帮助 LLM 理解哪些已完成和获取的数据
            if len(tool_results) > 0:
                completed_tasks_summary = []
                data_available = []
                
                for tr in tool_results:
                    func_name = tr["function"]
                    status = tr["result"].get("status", "unknown")
                    task_desc = tr.get("arguments", {}).get("task_description", "")[:60]
                    completed_tasks_summary.append(f"- {func_name}: {task_desc}... → {status}")
                    
                    # 如果成功，提取关键数据摘要
                    if status == "success":
                        result_preview = tr["result"].get("result", "")[:150]
                        if result_preview:
                            data_available.append(f"  Data from '{task_desc[:30]}...': {result_preview}...")
                
                summary_parts = [
                    f"✓ Completed {len(tool_results)} subtask(s) in this round:",
                    "\n".join(completed_tasks_summary)
                ]
                
                if data_available:
                    summary_parts.append("\n📊 Available data:")
                    summary_parts.extend(data_available)
                
                summary_parts.append(
                    "\n⚠️ IMPORTANT: "
                    "1) Do NOT repeat these completed subtasks. "
                    "2) Use the data above to complete remaining parts of the original task. "
                    "3) If all parts are done, confirm completion in your thought."
                )
                
                summary_text = "\n".join(summary_parts)
                
                messages.append({
                    "role": "user",
                    "content": summary_text
                })
                self.task_logger.info(f"[SUMMARY] {summary_text}")
            
            # ========== 阶段3: Response解析到执行完毕 ==========
            parsing_and_execution_start_time = model_end_time
            
            # 记录 Plan Agent 轮次到 recorder
            action_start_time = model_end_time  # 使用 API 调用结束时间作为 action 开始时间
            action_end_time = time.time()
            parsing_and_execution_time = action_end_time - parsing_and_execution_start_time
            round_log["timing"]["parsing_and_execution_time"] = parsing_and_execution_time
            self.task_logger.info(f"[TIMING] Parsing & Execution: {parsing_and_execution_time:.3f}s")
            
            # 总时间
            total_round_time = action_end_time - round_timestamp
            round_log["timing"]["total_round_time"] = total_round_time
            self.task_logger.info(f"[TIMING] Total Round: {total_round_time:.3f}s")
            self.task_logger.info(f"[TIMING] Breakdown: Prep={round_log['timing']['preparation_time']:.3f}s + API={round_log['timing']['api_call_time']:.3f}s + Parse&Exec={round_log['timing']['parsing_and_execution_time']:.3f}s")
            
            # 收集本轮所有 dispatched agents
            dispatched_agents = []
            for tr in tool_results:
                func_name = tr["function"]
                agent_id = func_name.replace("call_", "")
                dispatched_agents.append(agent_id)
            
            self.recorder.add_plan_agent_round(
                round_num=round_num,
                model_start_time=model_start_time,  # API 调用开始时间
                model_end_time=model_end_time,      # API 调用结束时间
                response=message.content or "",
                thought={
                    "analysis": message.content[:500] if message.content else "",
                    "tool_calls": len(message.tool_calls) if message.tool_calls else 0
                },
                action_start_time=action_start_time,  # 工具执行开始时间
                action_end_time=action_end_time,      # 工具执行结束时间
                dispatched_agents=dispatched_agents,
                messages=messages.copy(),  # 添加messages记录
                dependencies=dependencies  # 添加依赖关系记录
            )
            
            # 为每个 GUI Agent 添加记录
            for tool_result in tool_results:
                agent_id = tool_result["function"].replace("call_", "")
                task_desc = tool_result.get("arguments", {}).get("task_description", "")
                result_data = tool_result.get("result", {})
                status = result_data.get("status", "failure")
                steps = result_data.get("steps", [])
                
                # 获取 VM 分配信息，转换为 device_id（支持VM1-VM5）
                vm_assigned = tool_result.get("vm_assigned", "")
                if "VM1" in vm_assigned:
                    device_id = "Desktop-0"  # VM1 对应 Desktop-0
                elif "VM2" in vm_assigned:
                    device_id = "Desktop-1"  # VM2 对应 Desktop-1
                elif "VM3" in vm_assigned:
                    device_id = "Desktop-2"  # VM3 对应 Desktop-2
                elif "VM4" in vm_assigned:
                    device_id = "Desktop-3"  # VM4 对应 Desktop-3
                elif "VM5" in vm_assigned:
                    device_id = "Desktop-4"  # VM5 对应 Desktop-4
                else:
                    device_id = "Desktop-0"  # 默认
                
                # 获取实际的执行时间戳
                exec_start = tool_result.get("start_timestamp", action_start_time)
                exec_end = tool_result.get("end_timestamp", action_end_time)
                
                # 获取详细的轮次时间信息
                rounds_timing = result_data.get("rounds_timing", [])
                
                # 获取实际使用的 model_name（从 result 中获取，如果没有则根据 agent_id 推断）
                model_name = result_data.get("model_name", "")
                if not model_name:
                    # 根据 agent_id 推断 model
                    # 从 registry 获取 GUI agent 配置
                    use_qwen = getattr(self.registry, 'use_qwen_gui', False)
                    use_gpt = getattr(self.registry, 'use_gpt_gui', False)
                    if use_qwen:
                        model_name = "qwen-vl-max"
                    elif use_gpt:
                        model_name = "gpt-5.2"
                    else:
                        model_name = "claude-opus-4-5"
                
                # 创建新的 GUI agent 调用记录，返回 agent_count
                from prompts.claude_computer_use import CLAUDE_SYSTEM_PROMPT
                
                # 使用Claude Computer Use的system prompt
                gui_system_prompt = CLAUDE_SYSTEM_PROMPT
                # 根据device_id设置gui_agent的agent_id（动态适配 num_agents）
                device_to_agent_id = {
                    f"Desktop-{i}": f"gui_agent_{i+1}"
                    for i in range(self.num_agents)
                }
                gui_agent_id = device_to_agent_id.get(device_id, agent_id)  # fallback到原agent_id
                
                agent_count = self.recorder.add_gui_agent(
                    agent_id=gui_agent_id,
                    task=task_desc,
                    model_name=model_name,
                    parent_round=round_num,
                    start_timestamp=exec_start,
                    end_timestamp=exec_end,
                    device_id=device_id,
                    system_prompt=gui_system_prompt
                )
                
                # 如果有详细的轮次时间信息，使用真实数据
                if rounds_timing:
                    for rt in rounds_timing:
                        round_idx = rt.get("round", 1) - 1  # 转换为0-based（这是本次调用内的索引）
                        # 查找对应的 step 信息
                        step_info = {}
                        if round_idx < len(steps):
                            step_info = steps[round_idx] if isinstance(steps[round_idx], dict) else {}

                        # 构建messages（优先使用 rounds_timing 中的原始数据）
                        if rt.get("messages"):
                            agent_messages = rt.get("messages")
                        else:
                            agent_messages = [
                                {"role": "system", "content": gui_system_prompt},
                                {"role": "user", "content": task_desc}
                            ]

                        # 截图URL（从rounds_timing或step_info中获取）
                        screenshot_url = rt.get("screenshot_url", step_info.get("screenshot_path", ""))
                        # 获取timing信息（preparation, api_call, parsing_and_execution）
                        timing_info = rt.get("timing", {})
                        # 获取 action description（优先从 rt 获取，如果没有则从 step_info 获取）
                        action_desc = rt.get("action", step_info.get("action", rt.get("code", "")))
                        # 获取完整的模型响应文本（优先从 rt.response_text，回退到 step_info.thought）
                        full_response = rt.get("response_text", step_info.get("thought", f"Round {round_idx + 1} thinking"))
                        # 获取结构化的动作列表
                        action_detail_list = rt.get("action_details", step_info.get("actions", []))
                        if isinstance(action_detail_list, list) and action_detail_list:
                            actions_for_recorder = [{"type": "gui_action", "description": str(a)} for a in action_detail_list]
                        else:
                            actions_for_recorder = [{"type": "gui_action", "description": action_desc}]
                        self.recorder.add_gui_agent_round(
                            agent_id=agent_id,
                            round_num=round_idx,
                            model_start_time=rt.get("think_start", exec_start),
                            model_end_time=rt.get("think_end", exec_start + 1),
                            response=full_response,
                            actions=actions_for_recorder,
                            action_start_time=rt.get("action_start", exec_start + 1),
                            action_end_time=rt.get("action_end", exec_end),
                            action_result={
                                "status": step_info.get("status", status),
                                "returncode": 0 if step_info.get("status") == "success" else 1,
                                "output": str(step_info.get("output", "")),
                                "error": ""
                            },
                            agent_count=agent_count,
                            messages=agent_messages,
                            screenshot_url=screenshot_url,
                            timing=timing_info
                        )
                else:
                    # 回退到估算模式（兼容旧版本）
                    agent_messages = [
                        {"role": "system", "content": gui_system_prompt},
                        {"role": "user", "content": task_desc}
                    ]
                    
                    self.recorder.add_gui_agent_round(
                        agent_id=agent_id,
                        round_num=0,
                        model_start_time=exec_start,
                        model_end_time=exec_start + 2,
                        response=f"Executing task: {task_desc[:100]}",
                        actions=[{"type": "gui_action", "description": ""}],
                        action_start_time=exec_start + 2,
                        action_end_time=exec_end,
                        action_result={
                            "status": status,
                            "returncode": 0 if status == "success" else 1,
                            "output": str(result_data.get("result", "")),
                            "error": result_data.get("error", "")
                        },
                        agent_count=agent_count,
                        messages=agent_messages,
                        screenshot_url="",
                        timing=None  # 回退模式没有timing信息
                    )
                
                # 完成agent记录
                self.recorder.finish_gui_agent(agent_id, status, agent_count=agent_count)
            
            # 记录此轮到 execution_log
            self.execution_log["rounds"].append(round_log)
            
            # agent_id 固定绑定 VM，无需每轮释放
            
            # 不在这里提前退出，让 LLM 决定是否还需要继续执行其他任务
            # 只有当达到最大轮次或 LLM 不再调用工具时才会自然结束
        
        elapsed_time = time.time() - start_time
        
        # 完成执行日志
        self.execution_log["end_time"] = datetime.now().isoformat()
        self.execution_log["end_timestamp"] = time.time()
        self.execution_log["elapsed_time"] = elapsed_time
        
        # 根据最后一轮的状态判断任务是否成功
        task_success = False
        if len(history) > 0:
            last_round = history[-1]
            # 检查最后一轮的所有tool调用是否成功
            if last_round.get("results"):
                all_success = all(r.get("result", {}).get("status") == "success" for r in last_round.get("results", []))
                task_success = all_success
            else:
                # 如果最后一轮没有工具调用，说明coordinator认为任务完成了
                task_success = True
        else:
            # 如果没有任何轮次，标记为失败
            task_success = False

        # P0-4: 若循环是因为 max_rounds 耗尽（非 break 退出）且 recorder 未设置答案，
        # 且最近一轮 subagent 全挂，则兜底为 INSUFFICIENT_EVIDENCE 避免瞎猜
        if not getattr(self.recorder, "final_answer", None):
            if _last_executed_round_all_failed(self.execution_log):
                self.task_logger.warning(
                    "[ABSTAIN] Max rounds reached and last round all-failed; "
                    "setting final_answer=INSUFFICIENT_EVIDENCE"
                )
                self.recorder.set_final_answer("INSUFFICIENT_EVIDENCE")

        # === 总结步骤：额外调用一次模型，从完整对话历史中提取简洁的最终答案 ===
        # P0-2 修复: 若 thought 分支已通过 [ANSWER DETECTED] 设置 final_answer，
        # 跳过二次模型调用，避免模型改写答案（如 Malaysia → myanmar）
        _has_prior_answer = bool(
            getattr(self.recorder, "final_answer", None)
            and str(self.recorder.final_answer).strip()
        )
        if _has_prior_answer:
            self.task_logger.info(
                f"[SUMMARY] Short-circuit: recorder already has final_answer="
                f"'{str(self.recorder.final_answer)[:100]}'. Skipping summary extraction."
            )
        elif len(history) > 0:
            try:
                summary_prompt = (
                    "The task execution is now complete. Based on ALL the information gathered "
                    "from the GUI agents above, please provide the FINAL ANSWER to the original task.\n\n"
                    "CRITICAL RULES:\n"
                    "1. Your answer MUST be wrapped in <answer></answer> tags.\n"
                    "2. The answer inside the tags should be as CONCISE as possible:\n"
                    "   - If the answer is a number, just write the number: <answer>3</answer>\n"
                    "   - If the answer is a name/keyword, just write it: <answer>EUR</answer>\n"
                    "   - If the answer is a file name, write it WITHOUT extension: <answer>meeting1</answer>\n"
                    "   - If multiple items, separate with commas: <answer>Samsung, Xiaomi</answer>\n"
                    "3. Do NOT include explanations, full sentences, or extra context inside <answer> tags.\n"
                    "4. If the task asks 'which file/document', answer with the file name (without extension).\n"
                    "5. Review ALL rounds of execution results carefully before answering.\n"
                    "6. ALWAYS answer in English, even if the task instruction is in Chinese."
                )
                messages.append({"role": "user", "content": summary_prompt})

                self.task_logger.info("[SUMMARY] Calling model for final answer extraction...")
                summary_start = time.time()
                from parallel_benchmark.utils.llm_determinism import (
                    LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
                )
                _summary_kwargs = dict(
                    model=self.coordinator_model,
                    messages=messages,
                    temperature=LLM_TEMPERATURE,
                    seed=LLM_SEED,
                    max_tokens=500,
                )
                assert_deterministic(_summary_kwargs)
                summary_response = self.vlm.chat.completions.create(**_summary_kwargs)
                summary_elapsed = time.time() - summary_start
                self.task_logger.info(f"[SUMMARY] API call took {summary_elapsed:.2f}s")

                # 累计 token usage
                if hasattr(summary_response, 'usage') and summary_response.usage is not None:
                    self.token_usage["prompt_tokens"] += getattr(summary_response.usage, 'prompt_tokens', 0) or 0
                    self.token_usage["completion_tokens"] += getattr(summary_response.usage, 'completion_tokens', 0) or 0
                    self.token_usage["total_tokens"] += getattr(summary_response.usage, 'total_tokens', 0) or 0

                summary_content = summary_response.choices[0].message.content or ""
                self.task_logger.info(f"[SUMMARY] Response:\n{summary_content[:500]}")

                # 提取 <answer> 标签
                answer_match = re.search(r"<answer>(.*?)</answer>", summary_content, re.DOTALL | re.IGNORECASE)
                if answer_match:
                    extracted = answer_match.group(1).strip()
                    self.task_logger.info(f"[SUMMARY] Extracted answer: '{extracted}'")
                    self.recorder.set_final_answer(extracted)
                else:
                    # === Fix 4: summary 未产出 <answer> 标签时的多层 fallback ===
                    self.task_logger.info("[SUMMARY] No <answer> tag found in summary response, trying fallback...")
                    fallback_answer = self._extract_answer_from_history(history, messages)
                    if fallback_answer:
                        self.task_logger.info(f"[SUMMARY] Fallback extracted answer: '{fallback_answer}'")
                        self.recorder.set_final_answer(fallback_answer)
                    else:
                        # 最终兜底：用 summary 内容，但去掉明显的思考/计划文本
                        cleaned = self._clean_summary_as_answer(summary_content)
                        self.task_logger.info(f"[SUMMARY] Using cleaned summary as last resort: '{cleaned[:100]}'")
                        self.recorder.set_final_answer(cleaned)

            except Exception as e:
                self.task_logger.info(f"[SUMMARY] Summary call failed: {e}")
                # 总结步骤失败时，尝试从历史中提取答案
                fallback_answer = self._extract_answer_from_history(history, messages)
                if fallback_answer:
                    self.task_logger.info(f"[SUMMARY] Fallback extracted answer after exception: '{fallback_answer}'")
                    self.recorder.set_final_answer(fallback_answer)
                elif not self.recorder.final_answer:
                    if self.execution_log["rounds"]:
                        last_round_log = self.execution_log["rounds"][-1]
                        if last_round_log.get("thought") and not last_round_log.get("tool_calls"):
                            cleaned = self._clean_summary_as_answer(last_round_log["thought"])
                            self.task_logger.info(f"Setting cleaned last round thought as answer (fallback): '{cleaned[:100]}'")
                            self.recorder.set_final_answer(cleaned)
        else:
            # 没有任何执行历史
            if not self.recorder.final_answer:
                if self.execution_log["rounds"]:
                    last_round_log = self.execution_log["rounds"][-1]
                    if last_round_log.get("thought") and not last_round_log.get("tool_calls"):
                        cleaned = self._clean_summary_as_answer(last_round_log["thought"])
                        self.task_logger.info(f"Setting cleaned last round thought as answer (fallback): '{cleaned[:100]}'")
                        self.recorder.set_final_answer(cleaned)

        # 完成 recorder 记录并保存
        self.recorder.finish_task(success=task_success)
        
        # 保存执行记录到文件 (保存到 logs/ 目录)
        try:
            # 获取 parallel_benchmark 目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            parallel_benchmark_dir = os.path.dirname(current_dir)
            logs_dir = os.path.join(parallel_benchmark_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            record_path = os.path.join(logs_dir, "execution_record.json")
            self.recorder.save_to_file(record_path)
            self.task_logger.info(f"[RECORDER] Saved execution record to: {record_path}")
        except Exception as e:
            self.task_logger.error(f"[RECORDER] Failed to save execution record: {e}")
        
        # 打印费用报告
        self._print_cost_report()
        
        return {
            "success": True,
            "task": task,
            "rounds": len(history),
            "history": history,
            "elapsed_time": elapsed_time,
            "execution_log": self.execution_log,
            "execution_record": self.recorder.get_record(),  # 添加精确时间记录
            "token_usage": {
                "plan_agent": dict(self.token_usage),
                "gui_agent": dict(self.gui_token_usage),
                "plan_agent_model": self.coordinator_model,
                "gui_agent_model": self.gui_agent_model,
            }
        }

    def _build_user_prompt(self, task: str, context: Optional[str]) -> str:
        """
        构建用户 prompt

        输入:
            task: 任务指令
            context: 可选的 oracle plan 或额外上下文

        输出:
            str: 拼接后的用户消息
        """
        parts = [f"TASK: {task}"]
        if context:
            parts.append(
                "\nORACLE PLAN (reference decomposition — you should follow this strategy):\n"
                + context
            )
        return "\n".join(parts)

    def _parse_dependencies_from_xml(self, content, tool_agent_counts, current_round):
        """
        从 LLM 输出的 XML 格式中解析依赖关系
        
        Args:
            content: LLM 的响应内容（可能包含 <dependencies> XML）
            tool_agent_counts: 工具调用 ID 到 agent_count 的映射
            current_round: 当前轮次号
            
        Returns:
            dependencies: 依赖关系字典，格式与 _analyze_dependencies 相同
        """
        import xml.etree.ElementTree as ET
        # 注意：re 使用文件顶部的全局 import，不要在此处局部导入

        if not content or "<dependencies>" not in content:
            return {}
        
        try:
            # 提取 <dependencies>...</dependencies> 块
            dep_match = re.search(r'<dependencies>(.*?)</dependencies>', content, re.DOTALL)
            if not dep_match:
                return {}
            
            xml_str = f"<dependencies>{dep_match.group(1)}</dependencies>"
            root = ET.fromstring(xml_str)
            
            dependencies = {}
            
            # 解析每个 <agent> 节点
            for agent_elem in root.findall('agent'):
                agent_id = agent_elem.get('id')  # 例如 "call_gui_agent_1"
                agent_round = int(agent_elem.get('round', current_round))
                
                if not agent_id:
                    continue
                
                # 查找对应的 tool_call，获取 task 描述
                task_desc = ""
                # agent_id 可能是 "call_gui_agent_1"，需要找到对应的实际 agent_count
                # 但在这里，我们直接使用 agent_id 作为 key
                
                depends_on_list = []
                
                # 解析 <depends_on> 节点
                for dep_elem in agent_elem.findall('depends_on'):
                    dep_agent_id = dep_elem.get('agent_id')
                    dep_round = int(dep_elem.get('round', 1))
                    dep_reason = dep_elem.get('reason', 'Dependency specified by LLM')
                    
                    if dep_agent_id:
                        depends_on_list.append({
                            "agent_id": dep_agent_id,
                            "round": dep_round,
                            "reason": dep_reason
                        })
                
                dependencies[agent_id] = {
                    "round": agent_round,
                    "task": task_desc,  # 可以后续从 tool_calls 填充
                    "depends_on": depends_on_list
                }
            
            self.task_logger.info(f"✅ Parsed {len(dependencies)} agent dependencies from XML")
            return dependencies
            
        except Exception as e:
            self.task_logger.warning(f"⚠️  XML parsing failed: {e}")
            return {}

    def _analyze_dependencies(self, tool_calls, history, thought_text, tool_agent_counts=None):
        """
        分析当前工具调用依赖于之前哪些结果（启发式方法，作为后备）
        
        Args:
            tool_calls: 当前要执行的工具调用列表
            history: 之前所有轮次的执行历史
            thought_text: 当前轮的 thought 内容
            tool_agent_counts: 当前工具调用对应的 agent_count 映射 {tool_call.id: agent_count}
            
        Returns:
            dependencies: 依赖关系字典，格式为:
            {
                "call_gui_agent_1": {
                    "round": 2,
                    "task": "任务描述",
                    "depends_on": [
                        {
                            "agent_id": "call_gui_agent_1",
                            "round": 1,
                            "reason": "依赖原因"
                        }
                    ]
                }
            }
        """
        dependencies = {}
        tool_agent_counts = tool_agent_counts or {}
        
        if not history:
            # 第一轮，没有历史依赖
            for tc in tool_calls:
                args = self._load_tool_arguments(tc)
                agent_count = tool_agent_counts.get(tc.id, f"{tc.function.name}_temp")
                dependencies[agent_count] = {
                    "round": 1,
                    "task": args.get("task_description", "")[:100],
                    "depends_on": []
                }
            return dependencies
        
        # 分析依赖关系
        for tc in tool_calls:
            args = self._load_tool_arguments(tc)
            task_desc = args.get("task_description", "")
            agent_count = tool_agent_counts.get(tc.id, f"{tc.function.name}_temp")
            
            depends_on_list = []
            
            # 检查依赖关键词
            keywords_indicating_dependency = [
                "previous", "earlier", "above", "from round",
                "之前", "上一轮", "刚才", "已经", "获得的", "得到的",
                "based on", "using the", "with the data",
                "result from", "output of", "data from", "combine", "merge"
            ]
            
            combined_text = (thought_text or "") + " " + task_desc
            combined_text_lower = combined_text.lower()
            
            has_dependency_keyword = any(
                keyword in combined_text_lower 
                for keyword in keywords_indicating_dependency
            )
            
            if has_dependency_keyword:
                # 查看历史，获取之前的 agent_count
                for hist_round in history:
                    round_num = hist_round["round"]
                    hist_dependencies = hist_round.get("dependencies", {})
                    
                    # 从历史的 dependencies 中获取所有 agent_id
                    prev_agent_ids = []
                    if isinstance(hist_dependencies, dict):
                        prev_agent_ids = list(hist_dependencies.keys())
                    
                    # 同时也从 tool_calls 获取信息用于匹配
                    for idx, prev_tc in enumerate(hist_round.get("tool_calls", [])):
                        prev_function = prev_tc["function"]
                        prev_task = prev_tc["arguments"].get("task_description", "")
                        
                        # 尝试获取对应的 agent_count
                        if idx < len(prev_agent_ids):
                            prev_agent_count = prev_agent_ids[idx]
                        else:
                            # 兜底：使用函数名
                            prev_agent_count = f"{prev_function}_r{round_num}"
                        
                        dependency_detected = False
                        reason = ""
                        
                        # 检查是否明确提到 round 号（re 使用文件顶部的全局 import）
                        round_mentions = re.findall(r'round\s*(\d+)', combined_text_lower)
                        if str(round_num) in round_mentions:
                            dependency_detected = True
                            reason = f"Explicitly mentioned Round {round_num}"
                        
                        # 检查是否提到了之前工具的类型
                        if not dependency_detected:
                            if "gui" in prev_function and "gui" in combined_text_lower:
                                if "data" in combined_text_lower or "result" in combined_text_lower:
                                    dependency_detected = True
                                    reason = "References GUI agent data"
                        
                        # 检查文件路径共享
                        if not dependency_detected and round_num == history[-1]["round"]:
                            file_pattern = r'(/[\w/]+\.[\w]+)|([\w_]+\.\w+)'
                            prev_files = set(re.findall(file_pattern, prev_task))
                            curr_files = set(re.findall(file_pattern, task_desc))
                            
                            if prev_files & curr_files:
                                dependency_detected = True
                                reason = "Uses same files"
                        
                        if dependency_detected:
                            # 检查是否已存在，避免重复
                            if not any(d["agent_id"] == prev_agent_count for d in depends_on_list):
                                depends_on_list.append({
                                    "agent_id": prev_agent_count,
                                    "round": round_num,
                                    "reason": reason
                                })
            
            # 获取当前是第几轮
            current_round = len(history) + 1
            
            dependencies[agent_count] = {
                "round": current_round,
                "task": task_desc[:100],
                "depends_on": depends_on_list
            }
        
        return dependencies

    def _execute_tool_calls(
        self,
        tool_calls: List[Any],
        max_rounds_per_subtask: int,
        timeout_per_subtask: int,
        round_log: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        执行工具调用（支持并行），使用缓冲机制避免日志交错。

        改进点（相比原版）：
        1. 每个子线程的 print 输出通过 ThreadLocalStdout 重定向到独立 StringIO
        2. 结果收集到 local_results 列表，全部完成后在主线程统一写入 round_log
        3. 子 Agent 日志按 agent_count 顺序 flush 到 task_logger

        Args:
            tool_calls: OpenAI 返回的工具调用列表
            max_rounds_per_subtask: 每个子任务的最大轮次
            timeout_per_subtask: 每个子任务的超时时间
            round_log: 轮次日志，用于记录执行细节

        Returns:
            工具执行结果列表
        """
        import io
        import threading as _threading

        agent_buffers = {}  # agent_count -> StringIO
        local_results = []
        buffers_lock = _threading.Lock()

        # 检查 stdout 是否支持 set_buffer/clear_buffer（duck-type 检查）
        stdout_proxy = None
        if hasattr(sys.stdout, 'set_buffer') and hasattr(sys.stdout, 'clear_buffer'):
            stdout_proxy = sys.stdout

        # 预生成 agent_count 映射
        tool_agent_counts = {}
        agent_type_counters = {}
        for tc in tool_calls:
            func_name = tc.function.name
            agent_type = "gui_agent" if "gui" in func_name.lower() else func_name
            if agent_type not in agent_type_counters:
                agent_type_counters[agent_type] = 0
            agent_type_counters[agent_type] += 1
            agent_count = f"{agent_type}_call_{agent_type_counters[agent_type]}"
            tool_agent_counts[tc.id] = agent_count

        # 提取当前轮的 thought 文本，传给 _execute_single_tool 作为 fallback
        current_thought = round_log.get("thought", "") or ""

        def execute_one(tool_call):
            """在工作线程中执行单个工具调用，捕获输出到缓冲区。"""
            ac = tool_agent_counts.get(tool_call.id, "unknown")
            buf = io.StringIO()

            with buffers_lock:
                agent_buffers[ac] = buf

            # 设置线程级 stdout 缓冲，拦截 GUI Agent 的 print
            if stdout_proxy:
                stdout_proxy.set_buffer(buf)

            try:
                result = self._execute_single_tool(
                    tool_call, max_rounds_per_subtask,
                    timeout_per_subtask, ac,
                    thought_text=current_thought
                )
                return {"agent_count": ac, "result": result}
            except Exception as e:
                self.task_logger.error(f"[{ac}] 工具执行异常: {e}")
                return {
                    "agent_count": ac,
                    "result": {
                        "tool_call_id": tool_call.id,
                        "function": tool_call.function.name,
                        "arguments": {},
                        "agent_count": ac,
                        "result": {
                            "status": "failure", "result": "", "steps": [],
                            "error": str(e)
                        }
                    }
                }
            finally:
                if stdout_proxy:
                    stdout_proxy.clear_buffer()

        # 并行执行
        thread_timeout = None if timeout_per_subtask <= 0 else timeout_per_subtask + 60

        with ThreadPoolExecutor(max_workers=min(len(tool_calls), self.max_workers)) as executor:
            futures = {executor.submit(execute_one, tc): tc for tc in tool_calls}
            for future in as_completed(futures):
                tc = futures[future]
                ac = tool_agent_counts.get(tc.id, "unknown")
                try:
                    local_results.append(future.result(timeout=thread_timeout))
                except TimeoutError:
                    self.task_logger.error(f"[{ac}] 线程超时 ({thread_timeout}s)")
                    local_results.append({
                        "agent_count": ac,
                        "result": {
                            "tool_call_id": tc.id,
                            "function": tc.function.name,
                            "arguments": {},
                            "agent_count": ac,
                            "result": {
                                "status": "failure", "result": "", "steps": [],
                                "error": f"Thread-level timeout after {thread_timeout}s"
                            }
                        }
                    })
                except Exception as e:
                    self.task_logger.error(f"[{ac}] 线程异常: {e}")
                    local_results.append({
                        "agent_count": ac,
                        "result": {
                            "tool_call_id": tc.id,
                            "function": tc.function.name,
                            "arguments": {},
                            "agent_count": ac,
                            "result": {
                                "status": "failure", "result": "", "steps": [],
                                "error": str(e)
                            }
                        }
                    })

        # 主线程：按 agent_count 排序，统一收集结果
        local_results.sort(key=lambda x: x["agent_count"])
        results = [lr["result"] for lr in local_results]

        # 按 agent_count 顺序 flush 缓冲日志到 task_logger
        for ac in sorted(agent_buffers.keys()):
            buf = agent_buffers[ac]
            buf_content = buf.getvalue().strip()
            if buf_content:
                self.task_logger.info(f"\n  [{ac}] {'─'*40}")
                for line in buf_content.split('\n'):
                    self.task_logger.info(f"    {line}")

        return results

    def _extract_answer_from_history(
        self,
        history: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        从执行历史中搜索已产出的 <answer> 标签，作为 summary 阶段的 fallback。

        搜索顺序（后产出的优先）：
        1. Plan Agent 各轮 response 中的 <answer> 标签（倒序扫描）
        2. messages 中 assistant 消息里的 <answer> 标签（倒序扫描）
        3. GUI Agent 返回结果中的 result 字段

        Args:
            history: Plan Agent 执行历史（各轮记录）
            messages: 完整对话消息列表

        Returns:
            提取到的答案字符串，未找到则返回 None
        """
        # 策略 1：从 execution_log 的 thought 中倒序搜索 <answer> 标签
        rounds = self.execution_log.get("rounds", [])
        for round_log in reversed(rounds):
            thought = round_log.get("thought", "") or ""
            match = re.search(r"<answer>(.*?)</answer>", thought, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()

        # 策略 2：从 messages 中 assistant 消息倒序搜索 <answer> 标签
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "") or ""
                match = re.search(r"<answer>(.*?)</answer>", content, re.DOTALL | re.IGNORECASE)
                if match:
                    return match.group(1).strip()

        # 策略 3：从 GUI Agent 的结果中提取（最后一轮优先）
        for round_record in reversed(history):
            for result_entry in reversed(round_record.get("results", [])):
                result_data = result_entry.get("result", {})
                if isinstance(result_data, dict):
                    result_text = result_data.get("result", "")
                    if result_text and isinstance(result_text, str):
                        # 检查 GUI Agent 返回的 result 中是否有 <answer> 标签
                        match = re.search(r"<answer>(.*?)</answer>", result_text, re.DOTALL | re.IGNORECASE)
                        if match:
                            return match.group(1).strip()

        return None

    def get_rounds_record(self) -> Dict[str, Any]:
        """
        导出完整的逐轮推理记录，用于写入 _rounds.json。

        输出:
            符合 spec 定义的 JSON 结构，包含:
            - 任务元信息（models, num_agents）
            - 汇总统计（总轮次、总 token、总费用）
            - 每轮详细记录（thought 全文、token_usage、timing、tool_calls）
        """
        plan_cost = calculate_cost(self.token_usage, self.coordinator_model)
        gui_cost = calculate_cost(self.gui_token_usage, self.gui_agent_model or "unknown")

        return {
            "plan_agent_model": self.coordinator_model,
            "gui_agent_model": self.gui_agent_model or "unknown",
            "num_agents": self.num_agents,
            "total_rounds": len(self.execution_log.get("rounds", [])) if self.execution_log else 0,
            "total_elapsed_sec": self.execution_log.get("elapsed_time", 0) if self.execution_log else 0,
            "total_tokens": {
                "plan_agent": dict(self.token_usage),
                "gui_agent": dict(self.gui_token_usage),
            },
            "total_cost_usd": round(plan_cost["total_cost"] + gui_cost["total_cost"], 4),
            "rounds": self.execution_log.get("rounds", []) if self.execution_log else [],
        }

    @staticmethod
    def _clean_summary_as_answer(text: str) -> str:
        """
        清理 summary 文本，去掉明显的思考/计划性内容，尽量保留有效答案。

        处理规则：
        1. 如果文本中有 <answer> 标签，直接提取
        2. 如果文本以数字开头且较短（<20字符），直接返回
        3. 否则取最后一个句子（通常包含结论）

        Args:
            text: 原始 summary 文本

        Returns:
            清理后的文本
        """
        text = text.strip()
        if not text:
            return ""

        # 先尝试提取 <answer> 标签
        match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # 如果是短文本且看起来像答案（纯数字、简短词组），直接返回
        if len(text) <= 50:
            return text

        # 尝试从文本中提取最后一个有意义的结论句
        # 常见的计划/思考前缀，跳过这些内容
        thinking_prefixes = [
            "i'll ", "i will ", "let me ", "i need to ", "now i ",
            "the next step ", "my plan ", "i should ",
        ]
        text_lower = text.lower()
        if any(text_lower.startswith(p) for p in thinking_prefixes):
            # 整段都是思考过程，尝试取最后一句
            sentences = re.split(r'[.!?\n]', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            if sentences:
                # 从最后一句倒着找，跳过思考句
                for sent in reversed(sentences):
                    sent_lower = sent.lower()
                    if not any(sent_lower.startswith(p) for p in thinking_prefixes):
                        return sent
                # 全是思考句，返回最后一句
                return sentences[-1]

        return text

    def _load_tool_arguments(self, tool_call: Any) -> Dict[str, Any]:
        """
        安全解析 tool_call 的 arguments，避免 null/空字符串导致异常。
        """
        raw_args = getattr(tool_call.function, "arguments", None)
        if raw_args is None:
            return {}
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            stripped = raw_args.strip()
            if not stripped or stripped.lower() == "null":
                return {}
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError) as e:
                self.task_logger.warning(f"Failed to parse tool arguments JSON: {e}")
                self.task_logger.warning(f"Raw arguments (first 200 chars): {stripped[:200]}")
                return {}
            if not isinstance(parsed, dict):
                self.task_logger.warning(f"Parsed arguments is not a dict (type={type(parsed).__name__}), ignoring")
                return {}
            return parsed
        return {}

    def _execute_single_tool(
        self,
        tool_call: Any,
        max_rounds: int,
        timeout: int,
        agent_count: str = None,
        thought_text: str = ""
    ) -> Dict[str, Any]:
        """
        执行单个工具调用

        Args:
            tool_call: OpenAI 工具调用对象
            max_rounds: 最大执行轮次
            timeout: 超时时间
            agent_count: 预生成的唯一标识符（格式：{agent_type}_call_{counter}）
            thought_text: 当前轮的 LLM thought（用于 task_description 为空时的 fallback）

        Returns:
            工具执行结果
        """
        function_name = tool_call.function.name
        arguments = self._load_tool_arguments(tool_call)
        task_description = arguments.get("task_description", "")

        # Fallback: 当 task_description 为空时（常见于 arguments='null'），
        # 从当前轮的 LLM thought 中提取任务描述
        if not task_description.strip():
            if thought_text:
                # 取 thought 前 500 字符作为任务描述
                task_description = thought_text[:500].strip()
                self.task_logger.warning(f"⚠️  task_description was empty, using thought as fallback ({len(task_description)} chars)")
            else:
                task_description = "Execute the task as described in the conversation context."
                self.task_logger.warning(f"⚠️  task_description was empty and no thought available, using generic fallback")
        
        # 如果没有提供 agent_count，则生成一个临时的
        if not agent_count:
            agent_count = f"call_{function_name}_temp"
        
        self.task_logger.info(f"→ Executing {function_name} [{agent_count}]: {task_description[:80]}...")
        
        start_time = time.time()
        start_timestamp = time.time()
        
        # 创建工具调用日志
        tool_log = {
            "tool_call_id": tool_call.id,
            "function": function_name,
            "arguments": arguments,
            "start_timestamp": start_timestamp,
            "vm_assigned": None,
            "status": None,
            "result": None,
            "end_timestamp": None,
            "duration": None
        }
        
        try:
            if function_name == "call_gui_agent":
                # 调用 GUI Agent（通过 agent_id 显式指定 VM）
                self.task_logger.debug(f"Calling gui_agent with task: {task_description[:60]}...")

                # 从参数中获取 agent_id（1-based），映射到 vm_controllers（0-based）
                agent_id = arguments.get("agent_id")
                vm_count = len(self.vm_controllers)

                if agent_id is not None:
                    # 类型转换：防止 LLM 传入 float / string 等非法类型
                    try:
                        agent_id_int = int(agent_id)
                    except (ValueError, TypeError):
                        agent_id_int = None
                        self.task_logger.warning(f"Invalid agent_id type: {agent_id} ({type(agent_id).__name__})")

                    if agent_id_int is not None and 1 <= agent_id_int <= vm_count:
                        # 正常路径：agent_id 在有效范围内
                        vm_index = agent_id_int - 1
                    else:
                        # 超出范围或类型错误，fallback 到轮询
                        vm_index = self.next_gui_vm_index
                        self.next_gui_vm_index = (vm_index + 1) % vm_count
                        self.task_logger.warning(f"agent_id={agent_id} out of range [1, {vm_count}], "
                              f"fallback to round-robin → VM{vm_index + 1}")
                else:
                    # 兼容：如果 LLM 未传 agent_id，退回轮询分配
                    vm_index = self.next_gui_vm_index
                    self.next_gui_vm_index = (vm_index + 1) % vm_count
                    self.task_logger.warning(f"agent_id not provided, fallback to round-robin → VM{vm_index + 1}")

                vm_controller = self.vm_controllers[vm_index]
                self.task_logger.info(f"[AGENT] agent_id={agent_id} → GUI Agent {vm_index + 1} on {vm_controller.http_server}")

                # 禁用 VM 屏幕锁定和休眠，防止黑屏
                try:
                    disable_script = (
                        "import subprocess\n"
                        "cmds = [\n"
                        "    'xset s off',\n"
                        "    'xset -dpms',\n"
                        "    'xset s noblank',\n"
                        "    'DISPLAY=:0 gsettings set org.gnome.desktop.screensaver lock-enabled false',\n"
                        "    'DISPLAY=:0 gsettings set org.gnome.desktop.screensaver idle-activation-enabled false',\n"
                        "    'DISPLAY=:0 gsettings set org.gnome.desktop.session idle-delay 0',\n"
                        "]\n"
                        "for c in cmds:\n"
                        "    try:\n"
                        "        subprocess.run(c, shell=True, timeout=5, capture_output=True)\n"
                        "    except Exception:\n"
                        "        pass\n"
                    )
                    vm_controller.execute_python_command(disable_script)
                    self.task_logger.info(f"Disabled screen lock/sleep on VM{vm_index + 1}")
                except Exception as e:
                    self.task_logger.warning(f"Failed to disable screen lock on VM{vm_index + 1}: {e}")

                # 记录分配的 VM
                tool_log["vm_assigned"] = f"VM{vm_index + 1} (GUI Agent)"
                
                # 创建临时 registry，使用分配的 VM（参考原版 plan_agent.py 的做法）
                # 使用与主 registry 相同的 GUI Agent 设置
                from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry
                use_gpt = getattr(self.registry, 'use_gpt_gui', False)  # 默认使用 Claude
                use_qwen = getattr(self.registry, 'use_qwen_gui', False)  # 检查是否使用 Qwen
                use_doubao = getattr(self.registry, 'use_doubao_gui', False)  # 检查是否使用 Doubao
                use_kimi = getattr(self.registry, 'use_kimi_gui', False)  # 检查是否使用 Kimi
                use_seed18 = getattr(self.registry, 'use_seed18_gui', False)  # 检查是否使用 Seed 1.8
                use_gpt54 = getattr(self.registry, 'use_gpt54_gui', False)  # 检查是否使用 GPT-5.4
                gpt54_use_rid = getattr(self.registry, 'gpt54_use_response_id', True)
                gpt54_max_img = getattr(self.registry, 'gpt54_max_images', None)
                temp_registry = AgentToolRegistry(
                    vm_controller,
                    use_gpt_gui=use_gpt,
                    use_qwen_gui=use_qwen,
                    use_doubao_gui=use_doubao,
                    use_kimi_gui=use_kimi,
                    use_seed18_gui=use_seed18,
                    use_gpt54_gui=use_gpt54,
                    gpt54_use_response_id=gpt54_use_rid,
                    gpt54_max_images=gpt54_max_img,
                )
                
                try:
                    result = temp_registry.execute(
                        tool_name="gui_agent",
                        task=task_description,
                        max_rounds=max_rounds,
                        timeout=timeout
                    )
                    self.task_logger.debug(f"GUI agent result status: {result.get('status')}")
                    if result.get('status') == 'failure':
                        self.task_logger.debug(f"GUI agent error: {result.get('error')}")
                finally:
                    pass  # agent_id 固定绑定 VM，无需释放
            else:
                result = {
                    "status": "failure",
                    "result": "",
                    "steps": [],
                    "error": f"Unknown function: {function_name}"
                }
        except Exception as e:
            import traceback
            self.task_logger.error(f"Exception in tool execution: {e}")
            self.task_logger.error(traceback.format_exc())
            result = {
                "status": "failure",
                "result": "",
                "steps": [],
                "error": str(e)
            }
        
        elapsed = time.time() - start_time
        success = result.get('status') == 'success'
        self.task_logger.info(f"✓ Completed in {elapsed:.1f}s - Success: {success}")
        
        # 收集 GUI Agent 的 token usage（从结果中提取并累计）
        gui_usage = result.get("gui_token_usage")
        if gui_usage:
            self.gui_token_usage["prompt_tokens"] += gui_usage.get("prompt_tokens", 0)
            self.gui_token_usage["completion_tokens"] += gui_usage.get("completion_tokens", 0)
            self.gui_token_usage["total_tokens"] += gui_usage.get("total_tokens", 0)
            self.task_logger.info(f"[TOKEN] GUI Agent this call: prompt={gui_usage.get('prompt_tokens', 0)}, completion={gui_usage.get('completion_tokens', 0)}, cumulative_total={self.gui_token_usage['total_tokens']}")
        # 记录 GUI Agent 使用的模型名（取首个非空的）
        if not self.gui_agent_model:
            self.gui_agent_model = result.get("model_name", "")
        
        # 完成工具日志
        tool_log["end_timestamp"] = time.time()
        tool_log["duration"] = elapsed
        tool_log["status"] = "success" if success else "failure"
        tool_log["result"] = result
        tool_log["agent_count"] = agent_count  # 使用传入的 agent_count
        
        # 在返回日志中记录实际传给 GUI Agent 的 task_description（含 [Global Task Context]），
        # 而非原始的 arguments，便于排查任务传递是否完整
        logged_arguments = dict(arguments)
        logged_arguments["task_description"] = task_description

        return {
            "tool_call_id": tool_call.id,
            "function": function_name,
            "arguments": logged_arguments,
            "vm_assigned": tool_log.get("vm_assigned"),
            "result": result,
            "start_timestamp": start_timestamp,
            "end_timestamp": time.time(),
            "elapsed_time": elapsed,
            "agent_count": agent_count  # 添加 agent_count 到返回结果
        }

