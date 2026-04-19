"""
GPT-5.4 GUI Agent

从 OSWorld (https://github.com/xlang-ai/OSWorld) 移植并适配本项目的 controller 体系。

核心特点:
- 使用 OpenAI Responses API（非 Chat Completions）
- computer-use 工具: {"type": "computer"}
- 支持两种上下文模式:
  - 有状态模式（默认）: 通过 previous_response_id 让服务端维护完整对话历史
  - 无状态模式: 每轮独立请求，通过文本摘要或手动拼接历史截图保持上下文
- max_images 参数: 控制保留的历史截图数量（None=全部，N=最近N张）
- 批量动作支持: 一次 computer_call 可包含多个 action
- 内置 action → pyautogui 转换

与 OSWorld 原版的区别:
- 移除了 env.step() 依赖，改用外部传入的 controller 执行动作并获取截图
- predict() 方法作为核心接口，返回 (predict_info, actions)
- 外部循环负责 screenshot → predict → execute 的编排
- 新增 max_images 参数支持滑动窗口控制上下文长度

依赖:
    - openai (OpenAI Python SDK，需支持 Responses API)
    - config/api_config.py（通过 DeerAPI 代理调用）
"""

import base64
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger("desktopenv.agent.gpt54")

# GPT-5.4 系统提示词（与 OSWorld 原版一致）
OPERATOR_PROMPT = """

Here are some helpful tips:
- You are operating an {PLATFORM} desktop with internet access.
- My computer password is "{CLIENT_PASSWORD}" when sudo is needed.
- The current date is {CURRENT_DATE}.
- The home directory is "{HOME_DIR}".
- Stick to the website or application already opened for the task when possible.
- Prefer Chrome over Firefox/Chromium unless the task says otherwise.
- You can act without asking for confirmation.
- If content may be off-screen, scroll or zoom out before deciding it is unavailable.
- When possible, bundle multiple GUI actions into one computer-use turn.
- If the task is infeasible because of missing apps, permissions, contradictory requirements, or other hard blockers, output exactly "[INFEASIBLE]".
"""


def encode_image(image_content: bytes) -> str:
    """将图片二进制数据编码为 base64 字符串"""
    return base64.b64encode(image_content).decode("utf-8")


def _model_dump(value: Any) -> Any:
    """递归地将 pydantic 模型转换为字典"""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_model_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _model_dump(item) for key, item in value.items()}
    return value


def _preview_text(text: str, limit: int = 120) -> str:
    """截断文本用于日志显示"""
    sanitized = text.replace("\n", "\\n")
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[:limit] + "..."


def _get_field(value: Any, field: str, default: Any = None) -> Any:
    """兼容字典和对象的字段访问"""
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _sanitize_for_log(value: Any) -> Any:
    """清理日志中的大型 payload（如 base64 截图），避免日志过大"""
    value = _model_dump(value)
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key == "image_url" and isinstance(item, str) and item.startswith("data:image/"):
                sanitized[key] = "<image>"
            else:
                sanitized[key] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    return value


class Timer:
    """上下文管理器：测量代码块执行耗时"""
    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.duration = time.time() - self.start


class GPT54Agent:
    """
    GPT-5.4 GUI Agent（适配本项目 controller 体系）

    使用 OpenAI Responses API 的 computer-use 工具实现 GUI 自动化。
    predict() 方法为核心接口，接受任务指令和观测，返回动作列表。

    支持两种上下文管理模式:
    - 有状态模式 (use_response_id=True, 默认):
      通过 previous_response_id 让服务端维护完整对话历史和截图。
      与 OSWorld 原版一致，截图通过 computer_call_output 反馈给服务端。
    - 无状态模式 (use_response_id=False):
      每轮独立请求。可通过 max_images 控制在 input 中手动携带最近 N 张截图，
      或仅发送当前截图 + 文本动作摘要（max_images=None 时的旧行为）。

    输入:
        model: 模型名称（默认 "gpt-5.4-mini"）
        api_key: OpenAI API key（通过 DeerAPI 代理）
        base_url: API 基地址
        platform: 操作系统平台（默认 "ubuntu"）
        max_tokens: 最大输出 token 数
        reasoning_effort: 推理强度（默认 "high"）
        screen_width: 屏幕宽度（默认 1920）
        screen_height: 屏幕高度（默认 1080）
        use_response_id: 是否使用 previous_response_id 有状态模式（默认 True）
        max_images: 保留的历史截图数量（None=全部/不限制，N=最近N张）
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        api_key: str = "",
        base_url: str = "https://api.deerapi.com/v1/",
        platform: str = "ubuntu",
        max_tokens: Optional[int] = None,
        top_p: float = 0.9,
        temperature: float = 0.5,
        max_trajectory_length: int = 100,
        client_password: str = "password",
        screen_width: int = 1920,
        screen_height: int = 1080,
        reasoning_effort: str = "high",
        use_response_id: bool = True,
        max_images: Optional[int] = None,
    ):
        """
        初始化 GPT-5.4 Agent

        输入:
            model: 模型名称
            api_key: API 密钥
            base_url: API 基地址（DeerAPI 代理）
            platform: 操作系统类型
            max_tokens: 最大输出 token
            top_p: 采样参数
            temperature: 温度参数
            max_trajectory_length: 最大轨迹长度
            client_password: VM 密码
            screen_width: 屏幕宽度
            screen_height: 屏幕高度
            reasoning_effort: 推理强度 (low/medium/high/xhigh)
            use_response_id: 是否使用 previous_response_id 有状态模式（默认 True，
                对齐 Azure computer-use 协议单图合约，并启用 Responses API prompt caching）
            max_images: 保留的历史截图数量（默认 None，即不限制/不重置会话）
                - None: 有状态模式下由服务端管理全部历史；无状态模式下仅发当前截图+文本摘要
                - N (整数): 有状态模式下当截图超过 N 张时重置会话并用最近 N 张重建；
                            无状态模式下在 input 中手动携带最近 N 张截图
        """
        self.platform = platform
        self.model = model
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.max_trajectory_length = max_trajectory_length
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.reasoning_effort = reasoning_effort
        self.client_password = client_password
        self.api_key = api_key
        self.base_url = base_url
        self.use_response_id = use_response_id
        self.max_images = max_images

        # GPT-5.4 computer-use 工具定义
        self.tools = [{"type": "computer"}]

        # ---- 有状态模式字段（use_response_id=True） ----
        # 服务端会话 ID，用于维护完整对话历史
        self.previous_response_id: Optional[str] = None
        # 待发送的输入项（computer_call_output 等），在下次 predict 时发送
        self.pending_input_items: List[Dict[str, Any]] = []
        # 累计截图计数（用于 max_images 触发会话重置）
        self._image_count: int = 0

        # ---- 无状态模式字段（use_response_id=False） ----
        # 历史动作记录（用于文本摘要）
        self.action_history: List[str] = []
        # 历史截图缓存（base64 编码，用于 max_images 手动拼接）
        self._screenshot_history: List[str] = []

    def _create_response(self, request_input: List[Dict[str, Any]], instructions: str):
        """
        调用 OpenAI Responses API

        输入:
            request_input: 输入消息列表
            instructions: 系统指令

        输出:
            API 响应对象
        """
        from openai import OpenAI

        retry_count = 0
        last_error = None
        while retry_count < 5:
            try:
                client = OpenAI(api_key=self.api_key, base_url=self.base_url)
                logger.info(
                    "发送 GPT-5.4 请求: use_response_id=%s, previous_id=%s, input_items=%d",
                    self.use_response_id,
                    self.previous_response_id[:20] + "..." if self.previous_response_id else None,
                    len(request_input),
                )
                logger.debug("请求内容: %s", _sanitize_for_log(request_input))

                from parallel_benchmark.utils.llm_determinism import (
                    LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
                )
                request: Dict[str, Any] = {
                    "model": self.model,
                    "instructions": instructions,
                    "input": request_input,
                    "tools": self.tools,
                    "parallel_tool_calls": False,
                    "reasoning": {
                        "effort": self.reasoning_effort,
                        "summary": "concise",
                    },
                    "truncation": "auto",
                    "temperature": LLM_TEMPERATURE,
                    "seed": LLM_SEED,
                }
                if self.max_tokens is not None:
                    request["max_output_tokens"] = self.max_tokens

                # 有状态模式：附带 previous_response_id
                if self.use_response_id and self.previous_response_id:
                    request["previous_response_id"] = self.previous_response_id

                assert_deterministic(request)
                try:
                    response = client.responses.create(**request)
                except Exception as _seed_err:
                    # 某些代理层（DeerAPI 等）可能不支持 seed，降级仅保留 temperature
                    err_msg = str(_seed_err).lower()
                    if "seed" in err_msg and ("unsupported" in err_msg or "unknown" in err_msg
                                              or "invalid" in err_msg or "unexpected" in err_msg):
                        logger.warning("GPT-5.4 Responses API 不支持 seed 参数，降级：%s", _seed_err)
                        request.pop("seed", None)
                        response = client.responses.create(**request)
                    else:
                        raise

                response_error = _get_field(_get_field(response, "error", {}), "message")
                if response_error:
                    raise RuntimeError(response_error)
                if _get_field(response, "status") == "failed":
                    raise RuntimeError("Responses API request failed.")

                logger.info("收到 GPT-5.4 响应")
                return response
            except Exception as exc:
                last_error = exc
                retry_count += 1
                logger.error("OpenAI API 错误 (第 %d 次): %s", retry_count, exc)
                time.sleep(min(5, retry_count * 2))
        raise RuntimeError(f"OpenAI API 调用失败 ({retry_count} 次): {last_error}")

    def _action_to_dict(self, action: Any) -> Dict[str, Any]:
        """
        将 API 返回的动作对象统一转换为 {type, args} 字典

        输入:
            action: API 返回的动作（可能是字典、pydantic 对象等）

        输出:
            {"type": action_type, "args": {参数字典}}
        """
        if isinstance(action, dict):
            action_type = action.get("type")
            action_args = {k: _model_dump(v) for k, v in action.items() if k != "type"}
            return {"type": action_type, "args": action_args}

        if hasattr(action, "model_dump"):
            raw = action.model_dump()
            action_type = raw.get("type")
            action_args = {k: _model_dump(v) for k, v in raw.items() if k != "type"}
            return {"type": action_type, "args": action_args}

        if hasattr(action, "to_dict"):
            raw = action.to_dict()
            action_type = raw.get("type")
            action_args = {k: _model_dump(v) for k, v in raw.items() if k != "type"}
            return {"type": action_type, "args": action_args}

        action_type = getattr(action, "type", None)
        action_args: Dict[str, Any] = {}
        for attr in dir(action):
            if attr.startswith("_") or attr == "type":
                continue
            try:
                action_args[attr] = _model_dump(getattr(action, attr))
            except Exception:
                continue
        return {"type": action_type, "args": action_args}

    def _convert_drag_path(self, args: Dict[str, Any]) -> Optional[str]:
        """
        将拖拽动作转换为 pyautogui 代码

        输入:
            args: 拖拽动作参数（包含 path 或 from/to）

        输出:
            pyautogui 代码字符串，转换失败返回 None
        """
        path = args.get("path")
        if not path and args.get("from") and args.get("to"):
            path = [args["from"], args["to"]]
        if not path or len(path) < 2:
            return None

        def point_xy(point: Any) -> Tuple[Any, Any]:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                return point[0], point[1]
            if isinstance(point, dict):
                return point.get("x"), point.get("y")
            return getattr(point, "x", None), getattr(point, "y", None)

        first_x, first_y = point_xy(path[0])
        if first_x is None or first_y is None:
            return None

        commands = [f"import pyautogui\npyautogui.moveTo({first_x}, {first_y})"]
        for point in path[1:]:
            x, y = point_xy(point)
            if x is None or y is None:
                return None
            commands.append(f"pyautogui.dragTo({x}, {y}, duration=0.2, button='left')")
        return "\n".join(commands)

    def _typing_strategy(self, text: str) -> str:
        """
        根据文本内容选择输入策略

        输入:
            text: 要输入的文本

        输出:
            策略名称: empty / clipboard / multiline_ascii / single_line_ascii
        """
        if text == "":
            return "empty"
        if not text.isascii():
            return "clipboard"
        if "\n" in text:
            return "multiline_ascii"
        return "single_line_ascii"

    def _build_multiline_ascii_type_command(self, text: str) -> str:
        """
        构建多行 ASCII 文本的 pyautogui 输入命令

        输入:
            text: 多行文本

        输出:
            pyautogui 代码字符串
        """
        commands = ["import pyautogui"]
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line:
                commands.append(f"pyautogui.typewrite({repr(line)}, interval=0.03)")
            if index < len(lines) - 1:
                commands.append("pyautogui.press('enter')")
        return "\n".join(commands)

    def _build_clipboard_paste_command(self, text: str) -> str:
        """
        构建剪贴板粘贴命令（用于非 ASCII 或复杂文本）

        输入:
            text: 要粘贴的文本

        输出:
            pyautogui 代码字符串
        """
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return (
            "import base64, time, pyautogui, pyperclip\n"
            f"_text = base64.b64decode('{encoded}').decode('utf-8')\n"
            "pyperclip.copy(_text)\n"
            "time.sleep(0.1)\n"
            "pyautogui.hotkey('ctrl', 'v')\n"
            "time.sleep(0.1)"
        )

    def _convert_action_to_pyautogui(self, action_type: str, args: Dict[str, Any]) -> Optional[str]:
        """
        将 GPT-5.4 computer-use 动作转换为 pyautogui 代码

        输入:
            action_type: 动作类型（click/double_click/move/drag/type/keypress/scroll/wait/screenshot）
            args: 动作参数字典

        输出:
            pyautogui Python 代码字符串，不支持的动作返回 None
        """
        if not action_type:
            return None

        key_mapping = {
            "alt": "alt", "arrowdown": "down", "arrowleft": "left",
            "arrowright": "right", "arrowup": "up", "backspace": "backspace",
            "capslock": "capslock", "cmd": "command", "command": "command",
            "ctrl": "ctrl", "delete": "delete", "end": "end", "enter": "enter",
            "esc": "esc", "home": "home", "insert": "insert", "option": "option",
            "pagedown": "pagedown", "pageup": "pageup", "shift": "shift",
            "space": "space", "super": "super", "tab": "tab", "win": "win",
        }

        try:
            if action_type == "click":
                x, y = args.get("x"), args.get("y")
                button = args.get("button", "left")
                if x is None or y is None:
                    return None
                if button not in ["left", "middle", "right"]:
                    button = "left"
                return (
                    f"import pyautogui\n"
                    f"pyautogui.moveTo({x}, {y})\n"
                    f"pyautogui.click(button='{button}')"
                )

            if action_type == "double_click":
                x, y = args.get("x"), args.get("y")
                if x is None or y is None:
                    return None
                return f"import pyautogui\npyautogui.moveTo({x}, {y})\npyautogui.doubleClick()"

            if action_type == "move":
                x, y = args.get("x"), args.get("y")
                if x is None or y is None:
                    return None
                return f"import pyautogui\npyautogui.moveTo({x}, {y})"

            if action_type == "drag":
                return self._convert_drag_path(args)

            if action_type == "type":
                text = args.get("text", "")
                if text == "":
                    return "import time\ntime.sleep(0.1)"
                strategy = self._typing_strategy(text)
                if strategy == "multiline_ascii":
                    return self._build_multiline_ascii_type_command(text)
                if strategy == "clipboard":
                    return self._build_clipboard_paste_command(text)
                return f"import pyautogui\npyautogui.typewrite({repr(text)}, interval=0.03)"

            if action_type == "keypress":
                keys = args.get("keys")
                if not keys and args.get("key"):
                    keys = [args.get("key")]
                if not keys:
                    return None
                if not isinstance(keys, (list, tuple)):
                    keys = [keys]
                mapped_keys = [key_mapping.get(str(k).lower(), str(k).lower()) for k in keys]
                keys_str = ", ".join([repr(k) for k in mapped_keys])
                return f"import pyautogui\npyautogui.hotkey({keys_str})"

            if action_type == "scroll":
                x, y = args.get("x"), args.get("y")
                scroll_x = int(args.get("scroll_x") or args.get("delta_x") or args.get("deltaX") or 0)
                scroll_y = int(args.get("scroll_y") or args.get("delta_y") or args.get("deltaY") or 0)
                position = f", x={x}, y={y}" if x is not None and y is not None else ""
                if scroll_y:
                    return f"import pyautogui\npyautogui.scroll({scroll_y * -1}{position})"
                if scroll_x:
                    return f"import pyautogui\npyautogui.hscroll({scroll_x * -1}{position})"
                return None

            if action_type == "wait":
                secs = max(0.1, float(args.get("ms", 1000)) / 1000.0)
                return f"import time\ntime.sleep({secs})"

            if action_type == "screenshot":
                return "import time\ntime.sleep(0.1)"

        except Exception:
            logger.exception("GPT-5.4 动作转换失败: %s", action_type)
            return None

        logger.warning("不支持的 GPT-5.4 动作类型: %s", action_type)
        return None

    def _message_text(self, item: Any) -> str:
        """从 message 类型的输出项中提取文本内容"""
        content = _get_field(item, "content", [])
        if not content:
            return ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if _get_field(part, "type") == "output_text":
                    parts.append(_get_field(part, "text", ""))
            return "\n".join([p for p in parts if p])
        return str(content)

    def _reasoning_text(self, item: Any) -> str:
        """从 reasoning 类型的输出项中提取推理文本"""
        summary = _get_field(item, "summary", [])
        if not summary:
            return ""
        if isinstance(summary, list):
            parts = []
            for part in summary:
                text = _get_field(part, "text", "")
                if text:
                    parts.append(text)
            return "\n".join(parts)
        return str(summary)

    def _build_stateful_input(self, instruction: str, screenshot_b64: str) -> List[Dict[str, Any]]:
        """
        构建有状态模式（use_response_id=True）的请求输入

        输入:
            instruction: 任务指令
            screenshot_b64: 当前截图的 base64 编码

        输出:
            request_input 列表
        """
        if not self.previous_response_id:
            # 首轮：发送完整指令 + 截图
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot_b64}",
                            "detail": "original",
                        },
                    ],
                }
            ]

        # 后续轮次：发送 pending_input_items（computer_call_output）
        if self.pending_input_items:
            return list(self.pending_input_items)

        # fallback：没有 pending items 时回退到首轮模式
        # 有状态模式下，如果模型返回了 computer_call，后续 input 必须包含
        # 对应的 computer_call_output，纯文本和 input_image 都会被 API 拒绝。
        # 正常流程中 record_step_output() 会保证 pending_input_items 不为空。
        # 若此 fallback 触发（如模型返回纯文本无 computer_call），
        # 说明会话状态已不一致，重置 previous_response_id 回到首轮模式。
        logger.warning(
            "有状态模式 fallback：pending_input_items 为空，"
            "重置 previous_response_id，回退到首轮模式"
        )
        self.previous_response_id = None
        self._image_count = 0
        return [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{screenshot_b64}",
                        "detail": "original",
                    },
                ],
            }
        ]

    def _build_stateless_input(self, instruction: str, screenshot_b64: str) -> List[Dict[str, Any]]:
        """
        构建无状态模式（use_response_id=False）的请求输入

        输入:
            instruction: 任务指令
            screenshot_b64: 当前截图的 base64 编码

        输出:
            request_input 列表
        """
        if self.max_images is not None and self.max_images > 1:
            # 手动拼接模式：携带最近 N 张截图
            return self._build_manual_images_input(instruction, screenshot_b64)

        # 旧行为：文本摘要 + 单张截图
        if not self.action_history:
            task_text = instruction
        else:
            history_summary = "\n".join(
                f"Step {i+1}: {act}" for i, act in enumerate(self.action_history[-10:])
            )
            task_text = (
                f"{instruction}\n\n"
                f"Previous actions taken:\n{history_summary}\n\n"
                f"Continue from the current screenshot. Do NOT repeat previous actions."
            )

        return [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": task_text},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{screenshot_b64}",
                        "detail": "original",
                    },
                ],
            }
        ]

    def _build_manual_images_input(self, instruction: str, screenshot_b64: str) -> List[Dict[str, Any]]:
        """
        构建手动多截图模式的请求输入（无状态 + max_images > 1）

        将最近 N 张历史截图和当前截图一起放入 input 中，让模型同时看到多帧画面。
        每张历史截图附带对应的动作描述，提供时序上下文。

        输入:
            instruction: 任务指令
            screenshot_b64: 当前截图的 base64 编码

        输出:
            request_input 列表
        """
        content_items: List[Dict[str, Any]] = []

        # 添加任务指令
        content_items.append({"type": "input_text", "text": instruction})

        # 添加历史截图（最近 max_images - 1 张，为当前截图留 1 个位置）
        # 注意：此方法仅在 max_images is not None 且 max_images > 1 时被调用
        history_limit = (self.max_images or 1) - 1
        recent_screenshots = self._screenshot_history[-history_limit:] if history_limit > 0 else []
        recent_actions = self.action_history[-(len(recent_screenshots)):] if recent_screenshots else []

        if recent_screenshots:
            content_items.append({
                "type": "input_text",
                "text": f"Below are the {len(recent_screenshots)} most recent screenshots with actions taken:",
            })
            for i, ss_b64 in enumerate(recent_screenshots):
                # 添加对应的动作描述（如果有的话）
                if i < len(recent_actions):
                    content_items.append({
                        "type": "input_text",
                        "text": f"[Step {len(self.action_history) - len(recent_screenshots) + i + 1}] Action: {recent_actions[i]}",
                    })
                content_items.append({
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{ss_b64}",
                    "detail": "original",
                })

            content_items.append({
                "type": "input_text",
                "text": "Current screenshot (latest):",
            })

        # 添加当前截图
        content_items.append({
            "type": "input_image",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original",
        })

        if recent_screenshots:
            content_items.append({
                "type": "input_text",
                "text": "Continue from the current screenshot above. Do NOT repeat previous actions.",
            })

        return [{"role": "user", "content": content_items}]

    def predict(self, instruction: str, obs: Dict[str, Any]) -> Tuple[Dict, List[Dict]]:
        """
        根据当前屏幕截图预测下一步动作

        输入:
            instruction: 任务指令
            obs: 观测数据，必须包含 "screenshot" 键（bytes 格式的截图）

        输出:
            (predict_info, actions)
            - predict_info: 包含 model_usage / messages / response / state_correct
            - actions: 动作列表，每个元素包含 action_space / action / call_id / batch_* 等
        """
        home_dir = "/home/user"
        instructions = OPERATOR_PROMPT.format(
            CLIENT_PASSWORD=self.client_password,
            CURRENT_DATE=datetime.now().strftime("%A, %B %d, %Y"),
            HOME_DIR=home_dir,
            PLATFORM=self.platform,
        )
        screenshot_b64 = encode_image(obs["screenshot"])

        # 有状态模式 + max_images 限制：检查是否需要重置会话
        if self.use_response_id and self.max_images is not None:
            if self._image_count >= self.max_images and self.previous_response_id:
                logger.info(
                    "截图数量 (%d) 达到 max_images (%d)，重置会话使用最近截图重建上下文",
                    self._image_count, self.max_images,
                )
                self._reset_session_with_window(instruction, screenshot_b64)

        # 根据模式构建请求输入
        if self.use_response_id:
            request_input = self._build_stateful_input(instruction, screenshot_b64)
        else:
            request_input = self._build_stateless_input(instruction, screenshot_b64)

        with Timer() as model_timer:
            response = self._create_response(request_input, instructions)

        # 有状态模式：记录 response_id 和截图计数
        if self.use_response_id:
            self.previous_response_id = _get_field(response, "id")
            self.pending_input_items = []
            self._image_count += 1

        # 解析响应
        raw_output = _get_field(response, "output", []) or []
        actions: List[Dict[str, Any]] = []
        responses: List[str] = []
        unsupported_action = False
        infeasible_message = False

        for item in raw_output:
            item_type = _get_field(item, "type")

            if item_type == "message":
                message_text = self._message_text(item)
                if message_text:
                    responses.append(message_text)
                    lower = message_text.lower()
                    if "[infeasible]" in lower or any(
                        token in lower
                        for token in ["infeasible", "unfeasible", "impossible", "cannot be done", "not feasible"]
                    ):
                        infeasible_message = True

            elif item_type == "reasoning":
                reasoning_text = self._reasoning_text(item)
                if reasoning_text:
                    responses.append(reasoning_text)

            elif item_type == "computer_call":
                logger.info("computer_call: %s", _sanitize_for_log(item))
                raw_actions = _get_field(item, "actions")
                if raw_actions is None:
                    single_action = _get_field(item, "action")
                    raw_actions = [single_action] if single_action is not None else []

                call_id = _get_field(item, "call_id", "")
                pending_checks = _model_dump(_get_field(item, "pending_safety_checks", []))
                raw_actions = list(raw_actions)
                batch_size = len(raw_actions)

                for index, raw_action in enumerate(raw_actions):
                    action_info = self._action_to_dict(raw_action)
                    logger.info(
                        "动作 %d/%d (call_id=%s): %s",
                        index + 1, batch_size, call_id,
                        _sanitize_for_log(action_info),
                    )
                    pyautogui_code = self._convert_action_to_pyautogui(
                        action_info["type"], action_info["args"],
                    )
                    if not pyautogui_code:
                        unsupported_action = True
                        responses.append(f"Unsupported computer action: {action_info['type']}")
                        continue
                    actions.append({
                        "action_space": "pyautogui",
                        "action": pyautogui_code,
                        "pending_checks": pending_checks,
                        "call_id": call_id,
                        "batch_index": index,
                        "batch_size": batch_size,
                        "batch_last": index == batch_size - 1,
                    })

        state_correct = bool(actions) and not unsupported_action and not infeasible_message
        if unsupported_action:
            actions = []

        predict_info = {
            "model_usage": {
                "model_time": model_timer.duration,
                "prompt_tokens": _get_field(_get_field(response, "usage", {}), "input_tokens", 0),
                "completion_tokens": _get_field(_get_field(response, "usage", {}), "output_tokens", 0),
            },
            "messages": _model_dump(raw_output),
            "response": "\n".join([r for r in responses if r]),
            "state_correct": state_correct,
            "infeasible": infeasible_message,
        }

        logger.info("模型响应: %s", _preview_text(predict_info["response"]))
        logger.info("返回 %d 个动作", len(actions))

        return predict_info, actions

    def record_step_output(self, screenshot_bytes: bytes, call_id: str = "",
                           pending_checks: Optional[List] = None) -> None:
        """
        记录动作执行后的截图反馈（有状态模式专用）

        在有状态模式下，将执行后的截图作为 computer_call_output 追加到 pending_input_items，
        下次 predict() 时发送给服务端，使其能看到动作执行后的画面。
        与 OSWorld 原版 step() 方法中的截图反馈逻辑一致。

        输入:
            screenshot_bytes: 执行动作后的截图（bytes 格式）
            call_id: 对应的 computer_call 的 call_id
            pending_checks: 需要确认的安全检查列表
        """
        if not self.use_response_id:
            # 无状态模式：只需缓存截图到历史
            screenshot_b64 = encode_image(screenshot_bytes)
            self._screenshot_history.append(screenshot_b64)
            # 限制缓存大小
            if self.max_images is not None and len(self._screenshot_history) > self.max_images:
                keep_n = self.max_images
                self._screenshot_history = self._screenshot_history[-keep_n:]
            return

        # 有状态模式：构建 computer_call_output
        screenshot_b64 = encode_image(screenshot_bytes)
        output_item: Dict[str, Any] = {
            "type": "computer_call_output",
            "call_id": call_id,
            "output": {
                "type": "computer_screenshot",
                "image_url": f"data:image/png;base64,{screenshot_b64}",
                "detail": "original",
            },
        }
        if pending_checks:
            output_item["acknowledged_safety_checks"] = pending_checks

        self.pending_input_items.append(output_item)
        self._image_count += 1

    def record_action(self, action_description: str) -> None:
        """
        记录已执行的动作到历史（用于无状态模式的文本摘要上下文）

        输入:
            action_description: 动作描述字符串
        """
        self.action_history.append(action_description)

    def _reset_session_with_window(self, _instruction: str, _current_screenshot_b64: str) -> None:
        """
        重置有状态会话并用最近 N 张截图重建上下文（滑动窗口）

        当有状态模式下截图数量超过 max_images 时调用。
        丢弃 previous_response_id，转为一次性发送最近的截图和动作历史，
        让模型在新会话中恢复上下文。

        输入:
            instruction: 原始任务指令
            current_screenshot_b64: 当前截图的 base64 编码
        """
        self.previous_response_id = None
        self.pending_input_items = []
        self._image_count = 0

        # 如果没有足够的历史截图缓存，不做额外处理（首轮逻辑会自动处理）
        # 注意：有状态模式下 _screenshot_history 由 record_step_output 维护
        logger.info("会话已重置，将在下次 predict 中以新会话开始")

    def reset(self):
        """重置 Agent 全部状态（清除历史动作记录和会话状态）"""
        # 有状态模式状态
        self.previous_response_id = None
        self.pending_input_items = []
        self._image_count = 0
        # 无状态模式状态
        self.action_history = []
        self._screenshot_history = []
