import base64
import os
import re
import time
from typing import Any, cast, Optional, Dict
from PIL import Image
import io

from anthropic import (
    Anthropic,
    AnthropicBedrock,
    AnthropicVertex,
    APIError,
    APIResponseValidationError,
    APIStatusError,
)
from anthropic.types.beta import (
    BetaMessageParam,
    BetaTextBlockParam,
)
from parallel_benchmark.utils.llm_determinism import LLM_SEED, LLM_TEMPERATURE
from .utils import COMPUTER_USE_BETA_FLAG, COMPUTER_USE_TYPE, PROMPT_CACHING_BETA_FLAG, SYSTEM_PROMPT, SYSTEM_PROMPT_WINDOWS, APIProvider, PROVIDER_TO_DEFAULT_MODEL_NAME, get_model_name
from .utils import _response_to_params, _inject_prompt_caching, _maybe_filter_to_n_most_recent_images

import logging
logger = logging.getLogger("desktopenv.agent")

# MAX_HISTORY = 10
API_RETRY_TIMES = 500
API_RETRY_INTERVAL = 5


def _is_request_too_large_error(error_msg: str) -> bool:
    error_text = str(error_msg or "").lower()
    return any(
        marker in error_text
        for marker in (
            "413",
            "payload too large",
            "request entity too large",
            "requesttoolarge",
            "request too large",
            "25000000",
            "member must have length less than or equal to",
        )
    )


class AnthropicAgent:
    def __init__(self,
                 platform: str = "Ubuntu",
                 model: str = "claude-sonnet-4-5-20250929",
                 provider: APIProvider = APIProvider.ANTHROPIC,
                 max_tokens: int = 4096,
                 api_key: str = os.environ.get("ANTHROPIC_API_KEY", None),
                 base_url: str = None,  # None 时 SDK 使用官方 endpoint；调用方可通过 get_api_config("claude") 传入
                 system_prompt_override: Optional[str] = None,
                 system_prompt_suffix: str = "",
                 only_n_most_recent_images: Optional[int] = 10,
                 action_space: str = "claude_computer_use",
                 screen_size: tuple[int, int] = (1920, 1080),
                 no_thinking: bool = False,
                 use_isp: bool = False,
                 api_timeout: Optional[float] = None,
                 api_max_retries: int = 4,
                 api_retry_times: int = API_RETRY_TIMES,
                 temperature: Optional[float] = LLM_TEMPERATURE,
                 seed: Optional[int] = LLM_SEED,
                 top_p: Optional[float] = None,
                 *args, **kwargs
                 ):
        self.platform = platform
        self.action_space = action_space
        self.logger = logger
        self.class_name = self.__class__.__name__
        self.model_name = model
        self.provider = provider
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.base_url = base_url
        self.system_prompt_override = system_prompt_override
        self.system_prompt_suffix = system_prompt_suffix
        self.only_n_most_recent_images = only_n_most_recent_images
        self.messages: list[BetaMessageParam] = []
        self.screen_size = screen_size
        self.no_thinking = no_thinking
        self.use_isp = use_isp
        self.api_timeout = api_timeout
        self.api_max_retries = api_max_retries
        self.api_retry_times = api_retry_times
        self.temperature = temperature
        self.seed = seed
        self.top_p = top_p

        self.resize_factor = (
            screen_size[0] / 1280,  # Assuming 1280 is the base width
            screen_size[1] / 720   # Assuming 720 is the base height
        )

        # 调试用：保存最近一次 API 请求和响应的完整内容
        self.last_api_request = None   # dict: {model, messages, system, tools, ...}
        self.last_api_response = None  # raw response object

    def _get_sampling_params(self):
        """Get sampling parameters (temperature and/or top_p) - let API validate exclusivity"""
        params = {}
        if self.temperature is not None:
            params['temperature'] = self.temperature
        if self.top_p is not None:
            params['top_p'] = self.top_p
        return params

    def add_tool_result(self, tool_call_id: str, result: str, screenshot: bytes = None):
        """Add tool result to message history"""
        tool_result_content = [
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": [{"type": "text", "text": result}]
            }
        ]

        # Add screenshot if provided
        if screenshot is not None:
            screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
            tool_result_content[0]["content"].append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_base64
                }
            })

        self.messages.append({
            "role": "user",
            "content": tool_result_content
        })

    def _extract_raw_response_string(self, response) -> str:
        """Extract and concatenate raw response content into a single string."""
        raw_response_str = ""
        if response.content:
            for block in response.content:
                if hasattr(block, 'text') and block.text:
                    raw_response_str += f"[TEXT] {block.text}\n"
                elif hasattr(block, 'thinking') and block.thinking:
                    raw_response_str += f"[THINKING] {block.thinking}\n"
                elif hasattr(block, 'name') and hasattr(block, 'input'):
                    raw_response_str += f"[TOOL_USE] {block.name}: {block.input}\n"
                else:
                    raw_response_str += f"[OTHER] {str(block)}\n"
        return raw_response_str.strip()

    def parse_actions_from_tool_call(self, tool_call: Dict, raw_response: str = "") -> str:
        result = ""
        function_args = (
            tool_call["input"]
        )

        text = function_args.get("text")
        coordinate = function_args.get("coordinate")
        start_coordinate = function_args.get("start_coordinate")
        scroll_direction = function_args.get("scroll_direction")
        scroll_amount = function_args.get("scroll_amount")
        duration = function_args.get("duration")

        action = function_args.get("action")
        if not action:
            tool_name = (
                tool_call.get("name")
                if isinstance(tool_call, dict)
                else getattr(getattr(tool_call, "function", None), "name", None)
            )
            if tool_name and tool_name != "computer":
                action = tool_name
        if not action and duration is not None:
            action = "wait"
        if not action and text is not None:
            action = "type"
        action_conversion = {
            "click": "left_click",
            "left click": "left_click",
            "right click": "right_click"
        }
        action = action_conversion.get(action, action)
        if action == "key" and text is None:
            key_value = (
                function_args.get("combo")
                or function_args.get("key")
                or function_args.get("keys")
            )
            if isinstance(key_value, (list, tuple)):
                text = "+".join(str(key) for key in key_value)
            elif key_value is not None:
                text = str(key_value)

        def _coordinate_from_text(value):
            if not isinstance(value, str):
                return None
            match = re.fullmatch(
                r"\s*[\[(]?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*[\])]?\s*",
                value,
            )
            if not match:
                return None
            return (int(float(match.group(1))), int(float(match.group(2))))

        def _as_float(value, default: float) -> float:
            if value is None:
                return default
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _as_int(value, default: int) -> int:
            try:
                return int(_as_float(value, float(default)))
            except (TypeError, ValueError):
                return default

        def _coordinate_pair_from_values(x_value, y_value):
            try:
                return (int(float(x_value)), int(float(y_value)))
            except (TypeError, ValueError):
                return None

        def _normalize_coordinate(value):
            if isinstance(value, str):
                return _coordinate_from_text(value)
            if isinstance(value, dict):
                if "x" in value and "y" in value:
                    return _coordinate_pair_from_values(value.get("x"), value.get("y"))
                if "end_x" in value and "end_y" in value:
                    return _coordinate_pair_from_values(value.get("end_x"), value.get("end_y"))
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return _coordinate_pair_from_values(value[0], value[1])
            return None

        if coordinate is None:
            coordinate = (
                _coordinate_pair_from_values(function_args.get("x"), function_args.get("y"))
                or _coordinate_pair_from_values(function_args.get("end_x"), function_args.get("end_y"))
            )
        else:
            coordinate = _normalize_coordinate(coordinate) or coordinate

        if start_coordinate is None:
            start_coordinate = _coordinate_pair_from_values(
                function_args.get("start_x"),
                function_args.get("start_y"),
            )
        else:
            start_coordinate = _normalize_coordinate(start_coordinate) or start_coordinate

        text_coordinate = _coordinate_from_text(text)
        if coordinate is None and text_coordinate is not None and action in (
            "mouse_move",
            "left_click_drag",
            "left_click",
            "right_click",
            "double_click",
            "middle_click",
            "left_press",
            "triple_click",
        ):
            coordinate = text_coordinate
            text = None

        if action in ("key", "type", "hold_key") and text is not None and not isinstance(text, str):
            text = str(text)

        # resize coordinates if resize_factor is set
        if coordinate and self.resize_factor:
            coordinate = (
                int(coordinate[0] * self.resize_factor[0]),
                int(coordinate[1] * self.resize_factor[1])
            )
        if start_coordinate and self.resize_factor:
            start_coordinate = (
                int(start_coordinate[0] * self.resize_factor[0]),
                int(start_coordinate[1] * self.resize_factor[1])
            )

        if action == "left_mouse_down":
            result += "pyautogui.mouseDown()\n"
        elif action == "left_mouse_up":
            result += "pyautogui.mouseUp()\n"

        elif action == "hold_key":
            if not isinstance(text, str):
                raise ValueError(f"{text} must be a string")

            keys = text.split('+')
            for key in keys:
                key = key.strip().lower()
                result += f"pyautogui.keyDown('{key}')\n"
            expected_outcome = f"Keys {text} held down."

        # Handle mouse move and drag actions
        elif action in ("mouse_move", "left_click_drag"):
            if coordinate is None:
                raise ValueError(f"coordinate is required for {action}")
            if text is not None:
                raise ValueError(f"text is not accepted for {action}")
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
                raise ValueError(f"{coordinate} must be a tuple of length 2")
            if not all(isinstance(i, int) for i in coordinate):
                raise ValueError(f"{coordinate} must be a tuple of ints")

            x, y = coordinate[0], coordinate[1]
            if action == "mouse_move":
                result += (
                    f"pyautogui.moveTo({x}, {y}, duration={duration or 0.5})\n"
                )
                expected_outcome = f"Mouse moved to ({x},{y})."
            elif action == "left_click_drag":
                # If start_coordinate is provided, validate and move to start before dragging
                if start_coordinate:
                    if not isinstance(start_coordinate, (list, tuple)) or len(start_coordinate) != 2:
                        raise ValueError(f"{start_coordinate} must be a tuple of length 2")
                    if not all(isinstance(i, int) for i in start_coordinate):
                        raise ValueError(f"{start_coordinate} must be a tuple of ints")
                    start_x, start_y = start_coordinate[0], start_coordinate[1]
                    result += (
                        f"pyautogui.moveTo({start_x}, {start_y}, duration={duration or 0.5})\n"
                    )
                result += (
                    f"pyautogui.dragTo({x}, {y}, duration={duration or 0.5})\n"
                )
                expected_outcome = f"Cursor dragged to ({x},{y})."

        # Handle keyboard actions
        elif action in ("key", "type"):
            if text is None:
                raise ValueError(f"text is required for {action}")
            if not isinstance(text, str):
                raise ValueError(f"{text} must be a string")
            if coordinate is not None:
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
                    raise ValueError(f"{coordinate} must be a tuple of length 2")
                if not all(isinstance(i, int) for i in coordinate):
                    raise ValueError(f"{coordinate} must be a tuple of ints")
                x, y = coordinate[0], coordinate[1]
                result += f"pyautogui.click({x}, {y})\n"

            if action == "key":
                key_conversion = {
                    "page_down": "pagedown",
                    "page_up": "pageup",
                    "super_l": "win",
                    "super": "command",
                    "escape": "esc"
                }
                key_sequences = [seq for seq in re.split(r"\s+", text.strip()) if seq]
                if not key_sequences:
                    raise ValueError("key text is empty")
                for sequence in key_sequences:
                    keys = [key.strip().lower() for key in sequence.split('+') if key.strip()]
                    for key in keys:
                        key = key_conversion.get(key, key)
                        result += (f"pyautogui.keyDown('{key}')\n")
                    for key in reversed(keys):
                        key = key_conversion.get(key, key)
                        result += (f"pyautogui.keyUp('{key}')\n")
                expected_outcome = f"Key {text} pressed."
            elif action == "type":
                text_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
                result += (
                    "import base64\n"
                    f"_text = base64.b64decode('{text_b64}').decode('utf-8')\n"
                    "try:\n"
                    "    import pyperclip\n"
                    "    pyperclip.copy(_text)\n"
                    "    pyautogui.hotkey('ctrl', 'v')\n"
                    "except Exception:\n"
                    "    pyautogui.write(_text, interval=0.01)\n"
                )
                expected_outcome = f"Text {text} written."

        # Handle scroll actions
        elif action == "scroll":
            amount = _as_int(scroll_amount, 5)
            if amount == 0:
                amount = 5
            magnitude = abs(amount)
            if text is not None:
                result += (f"pyautogui.keyDown('{text.lower()}')\n")
            if scroll_direction in ("up", "down"):
                scroll_clicks = magnitude if scroll_direction == "up" else -magnitude
                if coordinate is None:
                    result += f"pyautogui.scroll({scroll_clicks})\n"
                else:
                    x, y = coordinate[0], coordinate[1]
                    result += f"pyautogui.scroll({scroll_clicks}, {x}, {y})\n"
            elif scroll_direction in ("left", "right"):
                scroll_clicks = magnitude if scroll_direction == "right" else -magnitude
                if coordinate is None:
                    result += f"pyautogui.hscroll({scroll_clicks})\n"
                else:
                    x, y = coordinate[0], coordinate[1]
                    result += f"pyautogui.hscroll({scroll_clicks}, {x}, {y})\n"
            elif scroll_direction is None:
                # Claude sometimes omits scroll_direction and sends only scroll_amount.
                # The sign is not stable across responses, so prefer the natural-language
                # action text when it names a direction. Otherwise default to scrolling
                # down because most directionless scrolls are result-page continuation.
                response_text = str(raw_response or "").lower()
                if "scroll up" in response_text or "scroll back up" in response_text:
                    scroll_clicks = magnitude
                elif "scroll down" in response_text or "down more" in response_text or "continue scrolling down" in response_text:
                    scroll_clicks = -magnitude
                else:
                    scroll_clicks = -magnitude if amount > 0 else amount
                if coordinate is None:
                    result += f"pyautogui.scroll({scroll_clicks})\n"
                else:
                    x, y = coordinate[0], coordinate[1]
                    result += f"pyautogui.scroll({scroll_clicks}, {x}, {y})\n"
            else:
                raise ValueError(f"Invalid scroll_direction: {scroll_direction}")
            if text is not None:
                result += (f"pyautogui.keyUp('{text.lower()}')\n")
            expected_outcome = "Scroll action finished"

        # Handle click actions
        elif action in ("left_click", "right_click", "double_click", "middle_click", "left_press", "triple_click"):
            # Handle modifier keys during click if specified
            if text:
                keys = text.split('+')
                for key in keys:
                    key = key.strip().lower()
                    result += f"pyautogui.keyDown('{key}')\n"
            if coordinate is not None:
                x, y = coordinate
                if action == "left_click":
                    result += (f"pyautogui.click({x}, {y})\n")
                elif action == "right_click":
                    result += (f"pyautogui.rightClick({x}, {y})\n")
                elif action == "double_click":
                    result += (f"pyautogui.doubleClick({x}, {y})\n")
                elif action == "middle_click":
                    result += (f"pyautogui.middleClick({x}, {y})\n")
                elif action == "left_press":
                    result += (f"pyautogui.mouseDown({x}, {y})\n")
                    result += ("time.sleep(1)\n")
                    result += (f"pyautogui.mouseUp({x}, {y})\n")
                elif action == "triple_click":
                    result += (f"pyautogui.tripleClick({x}, {y})\n")

            else:
                if action == "left_click":
                    result += ("pyautogui.click()\n")
                elif action == "right_click":
                    result += ("pyautogui.rightClick()\n")
                elif action == "double_click":
                    result += ("pyautogui.doubleClick()\n")
                elif action == "middle_click":
                    result += ("pyautogui.middleClick()\n")
                elif action == "left_press":
                    result += ("pyautogui.mouseDown()\n")
                    result += ("time.sleep(1)\n")
                    result += ("pyautogui.mouseUp()\n")
                elif action == "triple_click":
                    result += ("pyautogui.tripleClick()\n")
            # Release modifier keys after click
            if text:
                keys = text.split('+')
                for key in reversed(keys):
                    key = key.strip().lower()
                    result += f"pyautogui.keyUp('{key}')\n"
            expected_outcome = "Click action finished"

        elif action == "wait":
            wait_seconds = max(0.1, min(_as_float(duration, 0.5), 30.0))
            result += f"time.sleep({wait_seconds})\n"
            expected_outcome = f"Wait for {wait_seconds} seconds"
        elif action == "fail":
            result += "FAIL"
            expected_outcome = "Finished"
        elif action == "done":
            result += "DONE"
            expected_outcome = "Finished"
        elif action == "call_user":
            result += "CALL_USER"
            expected_outcome = "Call user"
        elif action == "screenshot":
            result += "pyautogui.sleep(0.1)\n"
            expected_outcome = "Screenshot taken"
        else:
            raise ValueError(f"Invalid action: {action}")

        return result

    def predict(self, task_instruction: str, obs: Dict = None, system: Any = None):
        system_text = (
            self.system_prompt_override
            if self.system_prompt_override is not None
            else (SYSTEM_PROMPT_WINDOWS if self.platform == 'Windows' else SYSTEM_PROMPT)
        )
        if self.system_prompt_suffix:
            system_text = f"{system_text} {self.system_prompt_suffix}"
        system = BetaTextBlockParam(
            type="text",
            text=system_text
        )

        # resize screenshot if resize_factor is set
        if obs and "screenshot" in obs:
            # Convert bytes to PIL Image
            screenshot_bytes = obs["screenshot"]
            screenshot_image = Image.open(io.BytesIO(screenshot_bytes))

            # Store original unresized screenshot for zoom processing
            obs["screenshot_original"] = screenshot_bytes

            # Calculate new size based on resize factor
            new_width, new_height = 1280, 720

            # Resize the image
            resized_image = screenshot_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Convert back to bytes
            output_buffer = io.BytesIO()
            resized_image.save(output_buffer, format='PNG')
            obs["screenshot"] = output_buffer.getvalue()


        if not self.messages:

            init_screenshot = obs
            init_screenshot_base64 = base64.b64encode(init_screenshot["screenshot"]).decode('utf-8')
            self.messages.append({
                "role": "user",
                "content": [
                    {
                    "type": "image",
                    "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": init_screenshot_base64,
                        },
                    },
                    {"type": "text", "text": task_instruction},
                ]
            })

        # Add tool_result for ALL tool_use blocks in the last message
        if self.messages:
            last_message_content = self.messages[-1]["content"]
            tool_use_blocks = [block for block in last_message_content if block.get("type") == "tool_use"]

            for i, tool_block in enumerate(tool_use_blocks):
                tool_input = tool_block.get("input", {})
                action = tool_input.get("action")
                is_last_tool = i == len(tool_use_blocks) - 1

                include_screenshot = None

                if obs:
                    if action == "screenshot":
                        # Screenshot action always gets regular screenshot
                        include_screenshot = obs.get("screenshot")
                    elif is_last_tool:
                        # Auto-screenshot: last tool gets regular screenshot (unless it's zoom, handled above)
                        include_screenshot = obs.get("screenshot")

                self.add_tool_result(
                    tool_block["id"],
                    f"Success",
                    screenshot=include_screenshot
                )

        enable_prompt_caching = False
        betas = [COMPUTER_USE_BETA_FLAG]

        # Add interleaved thinking beta if ISP is requested
        if self.use_isp:
            betas.append("interleaved-thinking-2025-05-14")
            logger.info(f"Added interleaved thinking beta. Betas: {betas}")

        image_truncation_threshold = 10
        if self.provider == APIProvider.ANTHROPIC:
            client = Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=self.api_max_retries,
                timeout=self.api_timeout,
            ).with_options(
                default_headers={"anthropic-beta": COMPUTER_USE_BETA_FLAG}
            )
            enable_prompt_caching = True
        elif self.provider == APIProvider.VERTEX:
            client = AnthropicVertex()
        elif self.provider == APIProvider.BEDROCK:
            client = AnthropicBedrock(
                # Authenticate by either providing the keys below or use the default AWS credential providers, such as
                # using ~/.aws/credentials or the "AWS_SECRET_ACCESS_KEY" and "AWS_ACCESS_KEY_ID" environment variables.
                aws_access_key=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                # aws_region changes the aws region to which the request is made. By default, we read AWS_REGION,
                # and if that's not present, we default to us-east-1. Note that we do not read ~/.aws/config for the region.
                aws_region=os.getenv('AWS_DEFAULT_REGION'),
            )

        if enable_prompt_caching:
            betas.append(PROMPT_CACHING_BETA_FLAG)
            _inject_prompt_caching(self.messages)
            image_truncation_threshold = 20
            system["cache_control"] = {"type": "ephemeral"}

        if self.only_n_most_recent_images:
            _maybe_filter_to_n_most_recent_images(
                self.messages,
                self.only_n_most_recent_images,
                min_removal_threshold=image_truncation_threshold,
            )

        # Configure tool settings - use modern computer tool for all models
        tool_config = {
            'name': 'computer',
            'type': COMPUTER_USE_TYPE,
            'display_width_px': 1280,
            'display_height_px': 720,
            'display_number': 1
        }

        tools = [
            tool_config,
        ] if self.platform == 'Ubuntu' else [
            tool_config,
        ]

        # Configure thinking mode based on user preferences
        if self.no_thinking:
            # Disable thinking mode - omit the thinking parameter
            extra_body = {}
            actual_max_tokens = self.max_tokens  # Use default when no thinking
            logger.info("Thinking mode: DISABLED")
        else:
            # Enable thinking mode (regular or interleaved)
            # Use consistent 2048 budget for both regular and ISP thinking
            budget_tokens = 2048

            # For regular thinking: max_tokens > budget_tokens (API requirement)
            # For ISP: budget_tokens can exceed max_tokens (represents total across all thinking blocks)
            if self.max_tokens <= budget_tokens:
                required_max_tokens = budget_tokens + 500  # Give some headroom
                logger.warning(f"Regular thinking requires max_tokens > budget_tokens. Increasing max_tokens from {self.max_tokens} to {required_max_tokens}")
                actual_max_tokens = required_max_tokens
            else:
                actual_max_tokens = self.max_tokens

            extra_body = {
                "thinking": {"type": "enabled", "budget_tokens": budget_tokens}
            }
            if self.use_isp:
                logger.info("Thinking mode: INTERLEAVED SCRATCHPAD (ISP)")
            else:
                logger.info("Thinking mode: REGULAR SCRATCHPAD")

        request_extra_body = dict(extra_body)
        if self.seed is not None:
            request_extra_body["seed"] = self.seed

        # 保存完整的 API 请求参数（供调试/日志记录）
        self.last_api_request = {
            "model": get_model_name(self.provider, self.model_name),
            "max_tokens": actual_max_tokens,
            "messages": self.messages,
            "system": [system],
            "tools": tools,
            "betas": betas,
            "extra_body": request_extra_body,
            **self._get_sampling_params(),
        }

        try:
            response = None

            for attempt in range(self.api_retry_times):
                try:
                    response = client.beta.messages.create(
                        max_tokens=actual_max_tokens,
                        messages=self.messages,
                        model=get_model_name(self.provider, self.model_name),
                        system=[system],
                        tools=tools,
                        betas=betas,
                        extra_body=request_extra_body,
                        **self._get_sampling_params()
                    )

                    # 保存原始响应（供调试/日志记录）
                    self.last_api_response = response

                    logger.info(f"Response: {response}")
                    break
                except (APIError, APIStatusError, APIResponseValidationError) as e:
                    error_msg = str(e)
                    logger.warning(f"[API-Error] Anthropic API (attempt {attempt+1}/{self.api_retry_times}): {error_msg}")

                    if _is_request_too_large_error(error_msg):
                        logger.warning("Detected 25MB limit error, automatically reducing image count")
                        current_image_count = self.only_n_most_recent_images
                        new_image_count = max(1, current_image_count // 2)  # Keep at least 1 image
                        self.only_n_most_recent_images = new_image_count

                        _maybe_filter_to_n_most_recent_images(
                            self.messages,
                            new_image_count,
                            min_removal_threshold=image_truncation_threshold,
                        )
                        logger.info(f"Image count reduced from {current_image_count} to {new_image_count}")

                    if attempt < self.api_retry_times - 1:
                        time.sleep(API_RETRY_INTERVAL)
                    else:
                        raise  # All attempts failed, raise exception to enter existing except logic

        except (APIError, APIStatusError, APIResponseValidationError) as e:
            logger.error(f"[API-Error] Anthropic API error: {str(e)}")
            logger.exception("[API-Error] traceback")
            try:
                logger.warning("[API-Error] Retrying with backup API key...")

                backup_client = Anthropic(
                    api_key=os.environ.get("ANTHROPIC_API_KEY_BACKUP"),
                    base_url=self.base_url,
                    max_retries=self.api_max_retries,
                    timeout=self.api_timeout,
                ).with_options(
                    default_headers={"anthropic-beta": COMPUTER_USE_BETA_FLAG}
                )
                response = backup_client.beta.messages.create(
                    max_tokens=actual_max_tokens,
                    messages=self.messages,
                    model=get_model_name(self.provider, self.model_name),
                    system=[system],
                    tools=tools,
                    betas=betas,
                    extra_body=request_extra_body,
                    **self._get_sampling_params()
                )

                logger.info("Successfully used backup API key")
            except Exception as backup_e:
                backup_error_msg = str(backup_e)
                logger.error(f"[API-Error] Backup API call also failed: {backup_error_msg}")
                logger.exception("[API-Error] backup traceback")

                # Check if backup API also has 25MB limit error
                if _is_request_too_large_error(backup_error_msg):
                    logger.warning("Backup API also encountered 25MB limit error, further reducing image count")
                    # Reduce image count by half again
                    current_image_count = self.only_n_most_recent_images
                    new_image_count = max(1, current_image_count // 2)  # Keep at least 1 image
                    self.only_n_most_recent_images = new_image_count

                    # Reapply image filtering
                    _maybe_filter_to_n_most_recent_images(
                        self.messages,
                        new_image_count,
                        min_removal_threshold=image_truncation_threshold,
                    )
                    logger.info(f"Backup API image count reduced from {current_image_count} to {new_image_count}")

                return "", [{
                    "action_type": "API_ERROR",
                    "raw_response": f"API-Error: primary={str(e)}; backup={backup_error_msg}",
                }]

        except Exception as e:
            logger.error(f"[API-Error] Unexpected error in Anthropic API: {str(e)}")
            logger.exception("[API-Error] traceback")
            return "", [{
                "action_type": "API_ERROR",
                "raw_response": f"API-Error: {str(e)}",
            }]

        if response is None:
            logger.error("[API-Error] Response is None after API call")
            return "", [{
                "action_type": "API_ERROR",
                "raw_response": "API-Error: response is None after all retries",
            }]

        response_params = _response_to_params(response)
        logger.info(f"Received response params: {response_params}")

        # Convert raw response to concatenated string for trajectory logging
        raw_response_str = self._extract_raw_response_string(response)

        # Store response in message history
        self.messages.append({
            "role": "assistant",
            "content": response_params
        })

        max_parse_retry = 3
        for parse_retry in range(max_parse_retry):
            actions: list[Any] = []
            reasonings: list[str] = []
            try:
                for content_block in response_params:
                    if content_block["type"] == "tool_use":
                        actions.append({
                            "name": content_block["name"],
                            "input": cast(dict[str, Any], content_block["input"]),
                            "id": content_block["id"],
                            "action_type": content_block.get("type"),
                            "command": self.parse_actions_from_tool_call(content_block, raw_response_str),
                            "raw_response": raw_response_str  # Add raw response to each action
                        })
                    elif content_block["type"] == "text":
                        reasonings.append(content_block["text"])
                if isinstance(reasonings, list) and len(reasonings) > 0:
                    reasonings = reasonings[0]
                else:
                    reasonings = ""

                # Check if the model indicated the task is infeasible
                if raw_response_str and "[INFEASIBLE]" in raw_response_str:
                    logger.info("Detected [INFEASIBLE] pattern in response, triggering FAIL action")
                    # Override actions with FAIL
                    actions = [{
                        "action_type": "FAIL",
                        "raw_response": raw_response_str
                    }]

                logger.info(f"Received actions: {actions}")
                logger.info(f"Received reasonings: {reasonings}")
                if len(actions) == 0:
                    actions = [{
                        "action_type": "DONE",
                        "raw_response": raw_response_str
                    }]
                return reasonings, actions
            except Exception as e:
                logger.warning(f"parse_actions_from_tool_call parsing failed (attempt {parse_retry+1}/3), will retry API request: {e}")
                # Remove the recently appended assistant message to avoid polluting history
                self.messages.pop()
                # Retry API request
                response = None
                for attempt in range(self.api_retry_times):
                    try:
                        response = client.beta.messages.create(
                            max_tokens=actual_max_tokens,
                            messages=self.messages,
                            model=get_model_name(self.provider, self.model_name),
                            system=[system],
                            tools=tools,
                            betas=betas,
                            extra_body=request_extra_body,
                            **self._get_sampling_params()
                        )

                        logger.info(f"Response: {response}")
                        break  # Success, exit retry loop
                    except (APIError, APIStatusError, APIResponseValidationError) as e2:
                        error_msg = str(e2)
                        logger.warning(f"Anthropic API error (attempt {attempt+1}/{self.api_retry_times}): {error_msg}")
                        if attempt < self.api_retry_times - 1:
                            time.sleep(API_RETRY_INTERVAL)
                        else:
                            raise
                response_params = _response_to_params(response)
                logger.info(f"Received response params: {response_params}")

                # Update raw response string for retry case (will be used in next loop iteration)
                raw_response_str = self._extract_raw_response_string(response)

                self.messages.append({
                    "role": "assistant",
                    "content": response_params
                })
                if parse_retry == max_parse_retry - 1:
                    logger.error(f"parse_actions_from_tool_call parsing failed 3 times consecutively, terminating: {e}")
                    actions = [{
                        "action_type": "FAIL",
                        "raw_response": f"Failed to parse actions from tool call after {max_parse_retry} attempts: {e}"
                    }]
                    return reasonings, actions
    def reset(self, _logger = None, *args, **kwargs):
        """
        Reset the agent's state.
        """
        global logger
        if _logger:
            logger = _logger
        else:
            logger = logging.getLogger("desktopenv.agent")
        self.messages = []
        logger.info(f"{self.class_name} reset.")
