"""
Claude GUI Agent using the Anthropic Messages / computer-use API shape.

This path is intended for official Anthropic-compatible endpoints, including
third-party gateways that expose the Anthropic SDK-compatible Messages API.
It is separate from claude_gui_agent_as_tool.py, which uses an OpenAI-compatible
chat/completions gateway.
"""

from __future__ import annotations

import inspect
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from .base_agent_tool import BaseAgentTool
from .result_utils import stringify_model_output
from config.api_config import get_api_config, get_model_name
from parallel_benchmark.utils.llm_determinism import LLM_SEED, LLM_TEMPERATURE


_TRANSIENT_API_ERROR_MARKERS = (
    "rate limit",
    "429",
    "timeout",
    "timed out",
    "overloaded",
    "temporarily",
    "try again",
    "500",
    "502",
    "503",
    "504",
    "529",
    "connection",
    "server error",
    "service unavailable",
)

_FATAL_API_ERROR_MARKERS = (
    "accountoverdue",
    "insufficient_quota",
    "payment required",
    "billing",
    "invalid api key",
    "authentication",
    "unauthorized",
    "permission denied",
    "401",
    "403",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _extract_api_error(actions: Any) -> str:
    if not actions:
        return ""
    if not isinstance(actions, list):
        actions = [actions]
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type", "") or "").upper()
        if action_type == "API_ERROR":
            return str(
                action.get("raw_response")
                or action.get("error")
                or action.get("message")
                or "API_ERROR"
            )
    return ""


def _is_retryable_api_error(error_text: str) -> bool:
    lower = (error_text or "").lower()
    if not lower:
        return False
    is_transient = any(marker in lower for marker in _TRANSIENT_API_ERROR_MARKERS)
    is_fatal = any(marker in lower for marker in _FATAL_API_ERROR_MARKERS)
    return is_transient and not (is_fatal and not is_transient)


def _gui_only_system_prompt() -> str:
    current_date = datetime.today().strftime("%A, %B %d, %Y")
    return f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using x86_64 architecture with internet access.
* You interact only through the visible desktop GUI using the computer tool.
* Available computer actions are screenshot, mouse_move, left_click, right_click, middle_click, double_click, triple_click, left_click_drag, left_mouse_down, left_mouse_up, hold_key, type, key, scroll, wait, done, and fail.
* To open the browser, click the Chrome icon. Chrome is installed on this system.
* When viewing a page, zoom out or scroll when needed before deciding content is unavailable.
* Do not ask users for clarification during task execution. Always take action using the available GUI controls.
* Chain predictable mouse and keyboard operations into one computer function call when possible. Take a screenshot only when the next step depends on observing the result.
* Complete tasks through visible application interfaces, menus, dialogs, and keyboard shortcuts.
* If the task contains a [Global Task Context] section, treat it as background for exact constraints and references. Focus on the sub-task before that section as the immediate goal.
* TASK FEASIBILITY: You can declare a task infeasible at any point during execution. If the task cannot be completed with the available visible GUI state, installed applications, permissions, or task requirements, output exactly "[INFEASIBLE]" anywhere in your response.
* The current date is {current_date}.
* Home directory of this Ubuntu system is '/home/user'.
* If the screen is locked or a visible authentication dialog asks for the computer password, use 'password'.
</SYSTEM_CAPABILITY>"""


class ClaudeAnthropicGUIAgentTool(BaseAgentTool):
    """Run a full GUI task through Anthropic-format computer use."""

    def execute(self, task: str, max_rounds: int = 15, timeout: int = 0) -> Dict:
        start_time = time.time()
        model_name = (
            os.environ.get("ABLATION_CLAUDE_ANTHROPIC_GUI_MODEL", "").strip()
            or os.environ.get("ABLATION_CLAUDE_GUI_MODEL", "").strip()
            or get_model_name("claude_anthropic_gui_agent")
            or "claude-sonnet-4-5-20250929"
        )

        def _result(**kwargs) -> Dict:
            result_dict = self.format_result(**kwargs)
            result_dict["model_name"] = model_name
            return result_dict

        try:
            from mm_agents.anthropic.main import AnthropicAgent
            from mm_agents.anthropic.utils import APIProvider
        except ImportError as exc:
            return _result(
                success=False,
                result="",
                steps=[],
                error=(
                    "Missing Anthropic SDK path. Install requirements.txt so the "
                    "`anthropic` package is available, then retry. "
                    f"Import error: {exc}"
                ),
            )

        api_config = get_api_config("claude")
        provider_name = (
            os.environ.get("ANTHROPIC_PROVIDER")
            or os.environ.get("CLAUDE_PROVIDER")
            or "anthropic"
        ).strip().lower()
        provider_map = {
            "anthropic": APIProvider.ANTHROPIC,
            "bedrock": APIProvider.BEDROCK,
            "vertex": APIProvider.VERTEX,
        }
        provider = provider_map.get(provider_name)
        if provider is None:
            return _result(
                success=False,
                result="",
                steps=[],
                error=(
                    f"Unsupported ANTHROPIC_PROVIDER={provider_name!r}. "
                    "Use anthropic, bedrock, or vertex."
                ),
            )

        api_key = api_config.get("api_key", "")
        base_url = api_config.get("base_url", "")
        if provider == APIProvider.ANTHROPIC and not api_key:
            return _result(
                success=False,
                result="",
                steps=[],
                error=(
                    "Missing Anthropic-compatible API key. Set ANTHROPIC_API_KEY "
                    "or CLAUDE_API_KEY, or configure anthropic.api_key in configs/api.yaml."
                ),
            )

        screen_size = self._get_screen_size()
        init_kwargs: Dict[str, Any] = {
            "platform": "Ubuntu",
            "model": model_name,
            "provider": provider,
            "max_tokens": _env_int("ANTHROPIC_GUI_MAX_TOKENS", 4096),
            "api_key": api_key or None,
            "system_prompt_override": _gui_only_system_prompt(),
            "system_prompt_suffix": "",
            "only_n_most_recent_images": _env_int("ANTHROPIC_GUI_MAX_IMAGES", 4),
            "screen_size": screen_size,
            "no_thinking": _env_bool("ANTHROPIC_GUI_NO_THINKING", False),
            "use_isp": _env_bool("ANTHROPIC_GUI_USE_ISP", False),
            "temperature": LLM_TEMPERATURE,
            "seed": LLM_SEED,
        }

        # The local vendored OSWorld copy accepts base_url; current upstream may not.
        signature = inspect.signature(AnthropicAgent.__init__)
        if "base_url" in signature.parameters and base_url:
            init_kwargs["base_url"] = base_url
        if "effort" in signature.parameters:
            init_kwargs["effort"] = os.environ.get("ANTHROPIC_GUI_EFFORT", "max")
        if "max_steps" in signature.parameters:
            init_kwargs["max_steps"] = max_rounds
        if "api_timeout" in signature.parameters:
            init_kwargs["api_timeout"] = float(os.environ.get("ANTHROPIC_GUI_API_TIMEOUT", "120"))
        if "api_max_retries" in signature.parameters:
            init_kwargs["api_max_retries"] = _env_int("ANTHROPIC_GUI_API_MAX_RETRIES", 2)
        if "api_retry_times" in signature.parameters:
            init_kwargs["api_retry_times"] = _env_int("ANTHROPIC_GUI_API_RETRY_TIMES", 4)

        try:
            agent = AnthropicAgent(**init_kwargs)
        except Exception as exc:
            return _result(
                success=False,
                result="",
                steps=[],
                error=f"Failed to initialize Anthropic-format Claude agent: {exc}",
            )

        steps: List[Dict[str, Any]] = []
        rounds_timing: List[Dict[str, Any]] = []
        thoughts: List[str] = []
        token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "input_tokens_including_cache": 0,
            "total_tokens": 0,
        }
        last_text = ""

        for round_num in range(max_rounds):
            round_start = time.time()
            if timeout > 0 and time.time() - start_time > timeout:
                return _result(
                    success=False,
                    result=last_text or f"Task timeout after {timeout} seconds",
                    steps=steps,
                    error="Timeout",
                    rounds_timing=rounds_timing,
                    gui_token_usage=token_usage,
                )

            screenshot_start = time.time()
            try:
                screenshot = self.controller.get_screenshot()
                if not screenshot:
                    raise RuntimeError("empty screenshot")
            except Exception as exc:
                return _result(
                    success=False,
                    result=f"Failed to get screenshot at round {round_num + 1}",
                    steps=steps,
                    error=str(exc),
                    rounds_timing=rounds_timing,
                    gui_token_usage=token_usage,
                )
            screenshot_time = time.time() - screenshot_start

            think_start = time.time()
            predict_attempts = max(1, _env_int("ANTHROPIC_GUI_PREDICT_RETRIES", 3))
            predict_backoff = max(1, _env_int("ANTHROPIC_GUI_PREDICT_RETRY_BACKOFF", 3))
            thought = None
            actions = None
            last_prediction_error = ""

            for attempt in range(predict_attempts):
                try:
                    thought, actions = agent.predict(task, obs={"screenshot": screenshot})
                except Exception as exc:
                    last_prediction_error = f"Anthropic agent prediction error: {exc}"
                    if attempt < predict_attempts - 1 and _is_retryable_api_error(str(exc)):
                        time.sleep(predict_backoff * (attempt + 1))
                        continue
                    return _result(
                        success=False,
                        result=f"Error in round {round_num + 1}",
                        steps=steps,
                        error=last_prediction_error,
                        rounds_timing=rounds_timing,
                        gui_token_usage=token_usage,
                    )

                api_error = _extract_api_error(actions)
                if api_error:
                    last_prediction_error = f"Anthropic agent API error: {api_error}"
                    if attempt < predict_attempts - 1 and _is_retryable_api_error(api_error):
                        time.sleep(predict_backoff * (attempt + 1))
                        continue
                    return _result(
                        success=False,
                        result=f"API error in round {round_num + 1}",
                        steps=steps,
                        error=last_prediction_error,
                        rounds_timing=rounds_timing,
                        gui_token_usage=token_usage,
                    )

                break

            think_end = time.time()

            if thought is None and actions is None:
                return _result(
                    success=False,
                    result=last_text,
                    steps=steps,
                    error="Anthropic agent returned no response",
                    rounds_timing=rounds_timing,
                    gui_token_usage=token_usage,
                )

            thought_text = stringify_model_output(thought)
            last_text = thought_text or last_text
            thoughts.append(thought_text)
            self._accumulate_usage(getattr(agent, "last_api_response", None), token_usage)

            action_status, commands, action_labels = self._execute_actions(actions)
            action_end = time.time()

            step_detail = {
                "round": round_num + 1,
                "timestamp": action_end,
                "thought": thought_text,
                "actions": action_labels,
                "code": "\n".join(commands),
                "status": action_status,
                "output": thought_text[:300],
            }
            steps.append(step_detail)

            rounds_timing.append({
                "round": round_num + 1,
                "think_start": think_start,
                "think_end": think_end,
                "think_duration": think_end - think_start,
                "action_start": think_end,
                "action_end": action_end,
                "action_duration": action_end - think_end,
                "screenshot_duration": screenshot_time,
                "total_duration": action_end - round_start,
                "thought": thought_text,
                "code": "\n".join(commands),
                "response_text": thought_text,
                "action_details": commands,
                "status": action_status,
            })

            print(f"\n{'=' * 60}")
            print(f"[Claude Anthropic GUI Agent] Round {round_num + 1}")
            print(f"{'=' * 60}")
            print(f"[Thought]: {thought_text}")
            print(f"[Actions]: {action_labels}")
            print(f"{'=' * 60}\n")

            if action_status == "success":
                return _result(
                    success=True,
                    result=last_text or "Task completed.",
                    steps=steps,
                    rounds_timing=rounds_timing,
                    gui_token_usage=token_usage,
                )
            if action_status == "failed":
                return _result(
                    success=False,
                    result=last_text,
                    steps=steps,
                    error="Agent returned FAIL or action execution failed",
                    rounds_timing=rounds_timing,
                    gui_token_usage=token_usage,
                )

            time.sleep(0.5)

        return _result(
            success=False,
            result=last_text,
            steps=steps,
            error=f"Reached maximum rounds ({max_rounds}) without completing the task.",
            rounds_timing=rounds_timing,
            gui_token_usage=token_usage,
        )

    def _get_screen_size(self) -> Tuple[int, int]:
        try:
            size = self.controller.get_vm_screen_size()
            if isinstance(size, dict):
                return int(size.get("width", 1920)), int(size.get("height", 1080))
        except Exception:
            pass
        return 1920, 1080

    def _execute_actions(self, actions: Any) -> Tuple[str, List[str], List[str]]:
        if not actions:
            return "waiting", [], []

        if not isinstance(actions, list):
            actions = [actions]

        commands: List[str] = []
        labels: List[str] = []

        for action in actions:
            if not isinstance(action, dict):
                labels.append(str(action))
                continue

            action_type = str(action.get("action_type", "") or "").upper()
            command = str(action.get("command", "") or "").strip()
            action_input = action.get("input", {})
            label = action_type or str(action_input or command or "action")
            labels.append(label)

            if action_type == "DONE" or command == "DONE":
                return "success", commands, labels
            if action_type == "FAIL" or command == "FAIL":
                return "failed", commands, labels

            if command:
                try:
                    result = self.controller.execute_python_command(command)
                    commands.append(command)
                    if result is None:
                        labels[-1] = f"{label}:executed_no_response"
                except Exception as exc:
                    commands.append(command)
                    labels[-1] = f"{label}:execution_error:{exc}"
                    return "failed", commands, labels

        return "executed" if commands else "waiting", commands, labels

    def _accumulate_usage(self, response: Any, token_usage: Dict[str, int]) -> None:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return

        def _get(name: str) -> int:
            if isinstance(usage, dict):
                value = usage.get(name, 0)
            else:
                value = getattr(usage, name, 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def _get_nested_int(container: Any, name: str) -> int:
            if isinstance(container, dict):
                value = container.get(name, 0)
            else:
                value = getattr(container, name, 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        prompt_tokens = _get("input_tokens") or _get("prompt_tokens")
        completion_tokens = _get("output_tokens") or _get("completion_tokens")
        cache_creation_tokens = _get("cache_creation_input_tokens")
        cache_read_tokens = _get("cache_read_input_tokens")

        if not cache_creation_tokens:
            cache_creation = (
                usage.get("cache_creation") if isinstance(usage, dict)
                else getattr(usage, "cache_creation", None)
            )
            if cache_creation:
                cache_creation_tokens = (
                    _get_nested_int(cache_creation, "ephemeral_1h_input_tokens")
                    + _get_nested_int(cache_creation, "ephemeral_5m_input_tokens")
                )

        input_tokens_including_cache = (
            prompt_tokens + cache_creation_tokens + cache_read_tokens
        )
        calculated_total_tokens = input_tokens_including_cache + completion_tokens
        reported_total_tokens = _get("total_tokens")

        token_usage.setdefault("cache_creation_input_tokens", 0)
        token_usage.setdefault("cache_read_input_tokens", 0)
        token_usage.setdefault("input_tokens_including_cache", 0)
        token_usage["prompt_tokens"] += prompt_tokens
        token_usage["completion_tokens"] += completion_tokens
        token_usage["cache_creation_input_tokens"] += cache_creation_tokens
        token_usage["cache_read_input_tokens"] += cache_read_tokens
        token_usage["input_tokens_including_cache"] += input_tokens_including_cache
        token_usage["total_tokens"] += max(
            calculated_total_tokens,
            reported_total_tokens,
        )
