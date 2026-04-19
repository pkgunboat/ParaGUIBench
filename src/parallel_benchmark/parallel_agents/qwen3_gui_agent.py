"""
Qwen3 GUI Agent — 基于 OSWorld 官方 Qwen3VLAgent 适配

核心改动（相比官方实现）：
- 仅保留 OpenAI 兼容后端（移除 DashScope 原生 SDK 依赖）
- 内嵌 smart_resize（修复官方不可用的 import 路径）
- 移除 google.api_core 依赖（改用手动重试）
- 新增 controller 集成（截图获取 + 动作执行）
- 新增 last_round_timing 计时（兼容 ExecutionRecorder）
- 新增 QA 答案提取（answer action + terminate answer_text）
- parse_response 返回三元组 (instruction, actions, sections)
- API 配置参数化（api_key / base_url 由外部传入）
"""

import base64
import json
import logging
import math
import os
import time
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import openai
from PIL import Image
from requests.exceptions import SSLError

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

MAX_RETRY_TIMES = 5
"""LLM 调用最大重试次数"""

# smart_resize 相关常量（Qwen3VL 使用 factor=32）
IMAGE_FACTOR = 32
"""图像对齐因子，Qwen3VL 要求宽高可被 32 整除"""

MAX_PIXELS = 16 * 16 * 4 * 12800
"""最大像素数上限（≈13M），超过时缩小图像"""

MIN_PIXELS = 100 * 28 * 28
"""最小像素数下限（≈78K），低于时放大图像"""

MAX_RATIO = 200
"""最大允许宽高比"""


# ==================== 图像处理工具函数 ====================

def round_by_factor(number: int, factor: int) -> int:
    """
    将 number 四舍五入到最近的 factor 的倍数

    Args:
        number: 待处理数值
        factor: 对齐因子

    Returns:
        int: factor 的倍数
    """
    return round(number / factor) * factor


def ceil_by_factor(number: int, factor: int) -> int:
    """
    将 number 向上取整到 factor 的倍数

    Args:
        number: 待处理数值
        factor: 对齐因子

    Returns:
        int: 大于等于 number 的最小 factor 倍数
    """
    return math.ceil(number / factor) * factor


def floor_by_factor(number: int, factor: int) -> int:
    """
    将 number 向下取整到 factor 的倍数

    Args:
        number: 待处理数值
        factor: 对齐因子

    Returns:
        int: 小于等于 number 的最大 factor 倍数
    """
    return math.floor(number / factor) * factor


def smart_resize(
    height: int,
    width: int,
    factor: int = IMAGE_FACTOR,
    min_pixels: int = MIN_PIXELS,
    max_pixels: int = MAX_PIXELS,
) -> Tuple[int, int]:
    """
    智能缩放图像尺寸，满足以下约束：
    1. 宽高均可被 factor 整除
    2. 总像素数在 [min_pixels, max_pixels] 范围内
    3. 尽量保持原始宽高比

    Args:
        height: 原始高度
        width: 原始宽度
        factor: 对齐因子（默认 32）
        min_pixels: 最小像素数
        max_pixels: 最大像素数

    Returns:
        Tuple[int, int]: (缩放后高度, 缩放后宽度)
    """
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(
            f"absolute aspect ratio must be smaller than {MAX_RATIO}, "
            f"got {max(height, width) / min(height, width)}"
        )
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(int(height / beta), factor)
        w_bar = floor_by_factor(int(width / beta), factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(int(height * beta), factor)
        w_bar = ceil_by_factor(int(width * beta), factor)
    return h_bar, w_bar


def encode_image(image_content: bytes) -> str:
    """
    将图像字节内容编码为 base64 字符串

    Args:
        image_content: 图像原始字节

    Returns:
        str: base64 编码字符串
    """
    return base64.b64encode(image_content).decode("utf-8")


def process_image(image_bytes: bytes) -> str:
    """
    处理截图：先 smart_resize 缩放，再编码为 base64

    Args:
        image_bytes: 原始截图字节

    Returns:
        str: 处理后的 base64 编码图像
    """
    image = Image.open(BytesIO(image_bytes))
    width, height = image.size

    resized_height, resized_width = smart_resize(
        height=height,
        width=width,
        factor=IMAGE_FACTOR,
        max_pixels=MAX_PIXELS,
    )

    image = image.resize((resized_width, resized_height))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processed_bytes = buffer.getvalue()

    return base64.b64encode(processed_bytes).decode("utf-8")


# ==================== Qwen3 GUI Agent ====================

class Qwen3GUIAgent:
    """
    基于 OSWorld 官方 Qwen3VLAgent 适配的 GUI Agent

    使用 OpenAI 兼容后端（DashScope compatible-mode/v1）调用 Qwen3-VL 模型，
    通过 <tool_call> XML 格式解析动作指令，支持 0-999 相对坐标系。

    与官方实现的主要差异：
    - 仅 OpenAI 兼容后端（移除 dashscope SDK 依赖）
    - API 配置参数化（通过构造函数传入）
    - 内嵌 smart_resize（修复官方 import 路径问题）
    - 新增 controller 集成和 execute_actions 参数
    - 新增 last_round_timing 计时信息
    - parse_response 返回三元组，支持 QA 答案提取
    """

    def __init__(
        self,
        model: str = "qwen3-vl",
        max_tokens: int = 32768,
        top_p: float = 0.9,
        temperature: float = 0.0,
        history_n: int = 4,
        coordinate_type: str = "relative",
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        controller=None,
        execute_actions: bool = True,
    ):
        """
        初始化 Qwen3 GUI Agent

        Args:
            model: 模型名称（默认 "qwen3-vl"，后续可替换为 "qwen3.5-vl"）
            max_tokens: 最大生成 token 数
            top_p: 采样参数
            temperature: 温度参数
            history_n: 保留的历史轮次数（含截图）
            coordinate_type: 坐标类型 "relative"(0-999) 或 "absolute"
            api_key: OpenAI 兼容 API 密钥（优先从参数获取，其次环境变量）
            base_url: OpenAI 兼容 API 地址
            controller: PythonController 实例（用于截图获取和动作执行）
            execute_actions: 是否在 predict() 内执行动作（默认 True）
        """
        self.model = model
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.history_n = history_n
        self.coordinate_type = coordinate_type

        # API 配置：优先使用传入参数，其次环境变量
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = base_url

        # 项目集成
        self.controller = controller
        self.execute_actions = execute_actions

        # 计时信息（每轮更新，供 Tool 层读取）
        self.last_round_timing: Optional[Dict] = None
        # 每轮 token 消耗（_call_llm 调用后更新）
        self.last_token_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 调试用：保存最近一次 API 请求和响应的完整内容
        self.last_api_request = None   # dict: {model, messages, max_tokens}
        self.last_api_response = None  # str: 模型原始返回文本

        # 历史记录
        self.thoughts: List[str] = []
        self.actions: List[str] = []
        self.observations: List[Dict] = []
        self.responses: List[str] = []
        self.screenshots: List[str] = []

    def predict(
        self, instruction: str, obs: Dict, step_idx: int = 0  # noqa: ARG002
    ) -> Tuple[str, List[str], Dict]:
        """
        根据当前截图和指令预测下一步动作

        执行流程：
        1. 处理截图（smart_resize 预处理）
        2. 构建历史消息
        3. 调用 LLM
        4. 解析响应（<tool_call> XML 格式）
        5. 可选：执行动作

        Args:
            instruction: 任务指令
            obs: 观测字典，必须包含 "screenshot" 键（bytes）
            step_idx: 当前步骤索引（用于日志）

        Returns:
            Tuple[str, List[str], Dict]:
                - response_text: 模型原始响应文本
                - pyautogui_actions: 解析后的动作列表（含 "DONE"/"WAIT"/"FAIL" 标记）
                - sections: 结构化信息字典 {thought, action, answer}
        """
        think_start = time.time()

        # 获取并处理截图
        screenshot_bytes = obs["screenshot"]
        image = Image.open(BytesIO(screenshot_bytes))
        width, height = image.size
        logger.info(f"Original screen resolution: {width}x{height}")

        processed_image = process_image(screenshot_bytes)
        processed_img = Image.open(BytesIO(base64.b64decode(processed_image)))
        processed_width, processed_height = processed_img.size
        logger.info(
            f"Processed image resolution: {processed_width}x{processed_height}"
        )

        self.screenshots.append(processed_image)

        # 构建历史 action 描述（仅文本摘要，不含完整历史消息）
        current_step = len(self.actions)
        history_start_idx = max(0, current_step - self.history_n)

        previous_actions = []
        for i in range(history_start_idx):
            if i < len(self.actions):
                previous_actions.append(f"Step {i+1}: {self.actions[i]}")
        previous_actions_str = (
            "\n".join(previous_actions) if previous_actions else "None"
        )

        # 构建 Prompt（保留官方格式）
        description_prompt_lines = [
            "Use a mouse and keyboard to interact with a computer, and take screenshots.",
            "* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. You must click on desktop icons to start applications.",
            "* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn't open, try wait and taking another screenshot.",
            (
                f"* The screen's resolution is {processed_width}x{processed_height}."
                if self.coordinate_type == "absolute"
                else "* The screen's resolution is 1000x1000."
            ),
            "* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.",
            "* If you tried clicking on a program or link but it failed to load even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.",
            "* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.",
        ]
        description_prompt = "\n".join(description_prompt_lines)

        action_description_prompt = """
* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order.
* `type`: Type a string of text on the keyboard.
* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.
* `left_click`: Click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.
* `right_click`: Click the right mouse button at a specified (x, y) pixel coordinate on the screen.
* `middle_click`: Click the middle mouse button at a specified (x, y) pixel coordinate on the screen.
* `double_click`: Double-click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `triple_click`: Triple-click the left mouse button at a specified (x, y) pixel coordinate on the screen (simulated as double-click since it's the closest action).
* `scroll`: Performs a scroll of the mouse scroll wheel.
* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).
* `wait`: Wait specified seconds for the change to happen.
* `terminate`: Terminate the current task and report its completion status.
* `answer`: Answer a question. Use this action to provide the answer for QA tasks.
        """

        # 工具定义（JSON schema）— 新增 answer action 和 answer_text 参数
        tools_def = {
            "type": "function",
            "function": {
                "name_for_human": "computer_use",
                "name": "computer_use",
                "description": description_prompt,
                "parameters": {
                    "properties": {
                        "action": {
                            "description": action_description_prompt,
                            "enum": [
                                "key", "type", "mouse_move", "left_click",
                                "left_click_drag", "right_click", "middle_click",
                                "double_click", "scroll", "wait", "terminate",
                                "answer",
                            ],
                            "type": "string",
                        },
                        "keys": {
                            "description": "Required only by `action=key`.",
                            "type": "array",
                        },
                        "text": {
                            "description": "Required only by `action=type`.",
                            "type": "string",
                        },
                        "coordinate": {
                            "description": "The x,y coordinates for mouse actions.",
                            "type": "array",
                        },
                        "pixels": {
                            "description": "The amount of scrolling.",
                            "type": "number",
                        },
                        "time": {
                            "description": "The seconds to wait.",
                            "type": "number",
                        },
                        "status": {
                            "description": "The status of the task.",
                            "type": "string",
                            "enum": ["success", "failure"],
                        },
                        "answer_text": {
                            "description": "The answer text for QA tasks. Required when action='answer' or action='terminate' for QA tasks.",
                            "type": "string",
                        },
                    },
                    "required": ["action"],
                    "type": "object",
                },
                "args_format": "Format the arguments as a JSON object.",
            },
        }

        # System prompt（保留官方 <tools> XML 格式）
        system_prompt = (
            "# Tools\n\n"
            "You may call one or more functions to assist with the user query.\n\n"
            "You are provided with function signatures within <tools></tools> XML tags:\n"
            "<tools>\n"
            + json.dumps(tools_def)
            + "\n</tools>\n\n"
            "For each function call, return a json object with function name and arguments "
            "within <tool_call></tool_call> XML tags:\n"
            "<tool_call>\n"
            '{"name": <function-name>, "arguments": <args-json-object>}\n'
            "</tool_call>\n\n"
            "# Response format\n\n"
            "Response format for every step:\n"
            "1) Action: a short imperative describing what to do in the UI.\n"
            "2) A single <tool_call>...</tool_call> block containing only the JSON: "
            '{"name": <function-name>, "arguments": <args-json-object>}.\n\n'
            "Rules:\n"
            "- Output exactly in the order: Action, <tool_call>.\n"
            "- Be brief: one sentence for Action.\n"
            "- Do not output anything else outside those parts.\n"
            "- If finishing, use action=terminate in the tool call.\n"
            "- For QA tasks, when you find the answer, use action=answer with answer_text parameter."
        )

        instruction_prompt = (
            f"Please generate the next move according to the UI screenshot, "
            f"instruction and previous actions.\n\n"
            f"Instruction: {instruction}\n\n"
            f"Previous actions:\n{previous_actions_str}"
        )

        # 构建 messages（保留官方历史管理逻辑）
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt}],
            }
        ]

        history_len = min(self.history_n, len(self.responses))
        if history_len > 0:
            history_responses = self.responses[-history_len:]
            history_screenshots = self.screenshots[-history_len - 1 : -1]

            for idx in range(history_len):
                if idx < len(history_screenshots):
                    screenshot_b64 = history_screenshots[idx]
                    img_url = f"data:image/png;base64,{screenshot_b64}"
                    if idx == 0:
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": img_url},
                                    },
                                    {"type": "text", "text": instruction_prompt},
                                ],
                            }
                        )
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": img_url},
                                    }
                                ],
                            }
                        )

                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": f"{history_responses[idx]}"}
                        ],
                    }
                )

            # 当前帧
            curr_img_url = f"data:image/png;base64,{processed_image}"
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": curr_img_url},
                        }
                    ],
                }
            )
        else:
            # 无历史：第一轮
            curr_img_url = f"data:image/png;base64,{processed_image}"
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": curr_img_url},
                        },
                        {"type": "text", "text": instruction_prompt},
                    ],
                }
            )

        # 调用 LLM
        response = self._call_llm(messages)
        think_end = time.time()

        logger.info(f"Qwen3 Output: {response}")
        self.responses.append(response)

        # 解析响应
        low_level_instruction, pyautogui_code, sections = self.parse_response(
            response, width, height, processed_width, processed_height
        )

        logger.info(f"Low level instruction: {low_level_instruction}")
        logger.info(f"Pyautogui code: {pyautogui_code}")

        self.actions.append(low_level_instruction)

        # 动作执行
        action_start = time.time()
        if self.execute_actions and self.controller and pyautogui_code:
            for code in pyautogui_code:
                if code in ("DONE", "WAIT", "FAIL"):
                    continue
                try:
                    logger.info(f"Executing: {code}")
                    self.controller.execute_python_command(code)
                except Exception as e:
                    logger.error(f"Action execution failed: {e}")
        action_end = time.time()

        # 更新计时信息
        self.last_round_timing = {
            "think_start": think_start,
            "think_end": think_end,
            "action_start": action_start,
            "action_end": action_end,
        }

        return response, pyautogui_code, sections

    def parse_response(
        self,
        response: str,
        original_width: Optional[int] = None,
        original_height: Optional[int] = None,
        processed_width: Optional[int] = None,
        processed_height: Optional[int] = None,
    ) -> Tuple[str, List[str], Dict]:
        """
        解析 LLM 响应，转换为 pyautogui 可执行代码

        解析流程：
        1. 提取 "Action:" 行作为 low_level_instruction
        2. 提取 <tool_call>...</tool_call> 中的 JSON
        3. 根据 action 类型生成对应的 pyautogui 代码
        4. 对坐标进行转换（0-999 相对坐标 → 实际屏幕像素）

        Args:
            response: 模型原始响应文本
            original_width: 原始截图宽度
            original_height: 原始截图高度
            processed_width: 处理后截图宽度
            processed_height: 处理后截图高度

        Returns:
            Tuple[str, List[str], Dict]:
                - low_level_instruction: 动作描述
                - pyautogui_code: pyautogui 代码列表
                - sections: 结构化信息 {thought, action, answer}
        """
        low_level_instruction = ""
        pyautogui_code: List[str] = []
        sections: Dict = {"thought": "", "action": "", "answer": None}

        if response is None or not response.strip():
            return low_level_instruction, pyautogui_code, sections

        def adjust_coordinates(x: float, y: float) -> Tuple[int, int]:
            """
            坐标转换：从模型输出坐标转换为实际屏幕像素坐标

            Args:
                x: 模型输出的 x 坐标
                y: 模型输出的 y 坐标

            Returns:
                Tuple[int, int]: 实际屏幕像素坐标 (x, y)
            """
            if not (original_width and original_height):
                return int(x), int(y)
            if self.coordinate_type == "absolute":
                if processed_width and processed_height:
                    x_scale = original_width / processed_width
                    y_scale = original_height / processed_height
                    return int(x * x_scale), int(y * y_scale)
                return int(x), int(y)
            # relative: 从 0-999 网格缩放到实际分辨率
            x_scale = original_width / 999
            y_scale = original_height / 999
            return int(x * x_scale), int(y * y_scale)

        def process_tool_call(json_str: str) -> None:
            """
            解析单个 tool_call JSON，生成对应的 pyautogui 代码

            Args:
                json_str: tool_call 的 JSON 字符串
            """
            try:
                tool_call = json.loads(json_str)
                if tool_call.get("name") != "computer_use":
                    return
                args = tool_call["arguments"]
                action = args["action"]

                if action == "left_click":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        pyautogui_code.append(f"pyautogui.click({adj_x}, {adj_y})")
                    else:
                        pyautogui_code.append("pyautogui.click()")

                elif action == "right_click":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        pyautogui_code.append(
                            f"pyautogui.rightClick({adj_x}, {adj_y})"
                        )
                    else:
                        pyautogui_code.append("pyautogui.rightClick()")

                elif action == "middle_click":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        pyautogui_code.append(
                            f"pyautogui.middleClick({adj_x}, {adj_y})"
                        )
                    else:
                        pyautogui_code.append("pyautogui.middleClick()")

                elif action == "double_click":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        pyautogui_code.append(
                            f"pyautogui.doubleClick({adj_x}, {adj_y})"
                        )
                    else:
                        pyautogui_code.append("pyautogui.doubleClick()")

                elif action == "type":
                    text = args.get("text", "")
                    # 使用 repr 防止特殊字符问题
                    pyautogui_code.append(
                        f"pyautogui.typewrite({repr(text)})"
                    )

                elif action == "key":
                    keys = args.get("keys", [])
                    if isinstance(keys, list):
                        cleaned_keys = []
                        for key in keys:
                            if isinstance(key, str):
                                # 清理可能的格式问题
                                if key.startswith("keys=["):
                                    key = key[6:]
                                if key.endswith("]"):
                                    key = key[:-1]
                                if key.startswith("['") or key.startswith('["'):
                                    key = key[2:] if len(key) > 2 else key
                                if key.endswith("']") or key.endswith('"]'):
                                    key = key[:-2] if len(key) > 2 else key
                                key = key.strip()
                                cleaned_keys.append(key)
                            else:
                                cleaned_keys.append(key)
                        keys = cleaned_keys

                    keys_str = ", ".join([f"'{key}'" for key in keys])
                    if len(keys) > 1:
                        pyautogui_code.append(f"pyautogui.hotkey({keys_str})")
                    else:
                        pyautogui_code.append(f"pyautogui.press({keys_str})")

                elif action == "scroll":
                    pixels = args.get("pixels", 0)
                    pyautogui_code.append(f"pyautogui.scroll({pixels})")

                elif action == "hscroll":
                    pixels = args.get("pixels", 0)
                    pyautogui_code.append(f"pyautogui.scroll({pixels})")

                elif action == "mouse_move":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        pyautogui_code.append(
                            f"pyautogui.moveTo({adj_x}, {adj_y})"
                        )
                    else:
                        pyautogui_code.append("pyautogui.moveTo(0, 0)")

                elif action == "left_click_drag":
                    if "coordinate" in args:
                        x, y = args["coordinate"]
                        adj_x, adj_y = adjust_coordinates(x, y)
                        duration = args.get("duration", 0.5)
                        pyautogui_code.append(
                            f"pyautogui.dragTo({adj_x}, {adj_y}, duration={duration})"
                        )
                    else:
                        pyautogui_code.append("pyautogui.dragTo(0, 0)")

                elif action == "wait":
                    pyautogui_code.append("WAIT")

                elif action == "terminate":
                    # 提取 answer_text（QA 任务）
                    answer_text = args.get("answer_text", "")
                    if answer_text:
                        sections["answer"] = answer_text

                    status = args.get("status", "success")
                    if status == "failure":
                        pyautogui_code.append("FAIL")
                    else:
                        pyautogui_code.append("DONE")

                elif action == "answer":
                    # QA 任务专用：提取答案并标记完成
                    answer_text = args.get(
                        "answer_text", args.get("text", "")
                    )
                    if answer_text:
                        sections["answer"] = answer_text
                    pyautogui_code.append("DONE")

            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse tool call: {e}")

        # 逐行解析响应
        lines = response.split("\n")
        inside_tool_call = False
        current_tool_call: List[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 提取 Action 描述
            if line.lower().startswith("action:"):
                if not low_level_instruction:
                    low_level_instruction = line.split("Action:")[-1].strip()
                    sections["action"] = low_level_instruction
                continue

            # <tool_call> XML 块解析
            if line.startswith("<tool_call>"):
                inside_tool_call = True
                continue
            elif line.startswith("</tool_call>"):
                if current_tool_call:
                    process_tool_call("\n".join(current_tool_call))
                    current_tool_call = []
                inside_tool_call = False
                continue

            if inside_tool_call:
                current_tool_call.append(line)
                continue

            # 兜底：直接尝试解析独立的 JSON 行
            if line.startswith("{") and line.endswith("}"):
                try:
                    json_obj = json.loads(line)
                    if "name" in json_obj and "arguments" in json_obj:
                        process_tool_call(line)
                except json.JSONDecodeError:
                    pass

        # 处理未闭合的 tool_call
        if current_tool_call:
            process_tool_call("\n".join(current_tool_call))

        # 如果没有 Action 行但有动作，从动作类型推断描述
        if not low_level_instruction and pyautogui_code:
            first_code = pyautogui_code[0]
            if first_code not in ("DONE", "WAIT", "FAIL") and "." in first_code:
                action_type = first_code.split(".", 1)[1].split("(", 1)[0]
                low_level_instruction = f"Performing {action_type} action"
                sections["action"] = low_level_instruction

        return low_level_instruction, pyautogui_code, sections

    def _call_llm(self, messages: List[Dict]) -> str:
        """
        调用 LLM（OpenAI 兼容后端），带手动重试

        Args:
            messages: OpenAI 格式的消息列表

        Returns:
            str: 模型响应文本，调用失败返回空字符串
        """
        client = openai.OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

        # 保存完整的 API 请求（供调试/日志记录）
        self.last_api_request = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }

        for attempt in range(1, MAX_RETRY_TIMES + 1):
            logger.info(
                f"[OpenAI] Generating content with model: {self.model} "
                f"(attempt {attempt}/{MAX_RETRY_TIMES})"
            )
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                )
                content = response.choices[0].message.content
                # 保存原始响应（供调试/日志记录）
                self.last_api_response = content
                # 提取 token usage
                if hasattr(response, 'usage') and response.usage is not None:
                    self.last_token_usage = {
                        "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) or 0,
                        "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                        "total_tokens": getattr(response.usage, 'total_tokens', 0) or 0,
                    }
                if content:
                    return content
                logger.warning("[OpenAI] Empty response content, retrying...")
            except (SSLError, openai.RateLimitError) as e:
                logger.warning(f"[OpenAI] Rate limit / SSL error: {e}")
                if attempt < MAX_RETRY_TIMES:
                    time.sleep(30)
                    continue
            except (openai.BadRequestError, openai.InternalServerError) as e:
                logger.error(f"[OpenAI] API error: {e}")
                if attempt < MAX_RETRY_TIMES:
                    time.sleep(5)
                    continue
            except Exception as e:
                logger.error(f"[OpenAI] Unexpected error: {e}")
                if attempt < MAX_RETRY_TIMES:
                    time.sleep(5)
                    continue
                break

        return ""

    def reset(self):
        """
        重置 Agent 状态（清空历史记录）
        """
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.responses = []
        self.screenshots = []
        self.last_round_timing = None
