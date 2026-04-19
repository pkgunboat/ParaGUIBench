"""
Kimi GUI Agent - 基于 OSWorld 官方 KimiAgent 实现
使用 Kimi K2.5 模型的专用 Prompt 和解析逻辑，通过 PythonController 控制远程虚拟机

官方 KimiAgent 特点：
- Thought → Action → Code 三段式输出格式
- 支持 thinking（reasoning_content）/ non-thinking 两种模式
- 输出 pyautogui 代码，支持相对/绝对坐标转换
- computer.terminate / computer.wait 特殊动作
"""

import re
import os
import ast
import time
import base64
import traceback
import logging
from typing import Dict, List, Tuple, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================
# Prompt 模板（来自 OSWorld 官方 KimiAgent）
# ============================================================

def encode_image(image_content):
    """
    将图片字节内容编码为 base64 字符串

    Args:
        image_content: 图片的字节数据

    Returns:
        str: base64 编码后的字符串
    """
    return base64.b64encode(image_content).decode("utf-8")


INSTRUCTION_TEMPLATE = "# Task Instruction:\n{instruction}\n\nPlease generate the next move according to the screenshot, task instruction and previous steps (if provided).\n"

STEP_TEMPLATE = "# Step {step_num}:\n"

SYSTEM_PROMPT_THINKING = """
You are a GUI agent. You are given an instruction, a screenshot of the screen and your previous interactions with the computer. You need to perform a series of actions to complete the task. The passoword of the computer is {password}.

For each step, provide your response in this format:
{thought}
## Action:
{action}
## Code:
{code}

In the code section, the code should be either pyautogui code or one of the following functions wrapped in the code block:
- {"name": "computer.wait", "description": "Make the computer wait for 20 seconds for installation, running code, etc.", "parameters": {"type": "object", "properties": {}, "required": []}}
- {"name": "computer.terminate", "description": "Terminate the current task and report its completion status", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["success", "failure"], "description": "The status of the task"}, "answer": {"type": "string", "description": "The answer of the task"}}, "required": ["status"]}}
""".strip()

SYSTEM_PROMPT_NON_THINKING = """
You are a GUI agent. You are given an instruction, a screenshot of the screen and your previous interactions with the computer. You need to perform a series of actions to complete the task. The passoword of the computer is {password}.

For each step, provide your response in this format:
## Thought
{thought}
## Action:
{action}
## Code:
{code}

In the code section, the code should be either pyautogui code or one of the following functions wrapped in the code block:
- {"name": "computer.wait", "description": "Make the computer wait for 20 seconds for installation, running code, etc.", "parameters": {"type": "object", "properties": {}, "required": []}}
- {"name": "computer.terminate", "description": "Terminate the current task and report its completion status", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["success", "failure"], "description": "The status of the task"}, "answer": {"type": "string", "description": "The answer of the task"}}, "required": ["status"]}}
""".strip()

THOUGHT_HISTORY_TEMPLATE_THINKING = "◁think▷{thought}◁/think▷## Action:\n{action}\n"
THOUGHT_HISTORY_TEMPLATE_NON_THINKING = "## Thought:\n{thought}\n\n## Action:\n{action}\n"


# ============================================================
# 响应解析函数（来自 OSWorld 官方 KimiAgent）
# ============================================================

def parse_response_to_cot_and_action(response, screen_size, coordinate_type, thinking: bool) -> Tuple[str, List[str], dict]:
    """
    解析 Kimi 模型的响应，提取 Thought、Action 和 Code

    官方解析逻辑：
    - thinking 模式：从 reasoning_content 提取 thought，从 content 提取 Action 和 Code
    - non-thinking 模式：从 content 中用正则分别提取 ## Thought / ## Action / ## Code

    Args:
        response: 模型返回的 message 字典，包含 content 和可选的 reasoning_content
        screen_size: 屏幕尺寸 (width, height)
        coordinate_type: 坐标类型 "relative" / "absolute" / "qwen25"
        thinking: 是否为 thinking 模式

    Returns:
        Tuple[str, List[str], dict]:
            - low_level_instruction: 动作描述文本
            - pyautogui_actions: 动作代码列表 ["DONE"] / ["WAIT"] / ["FAIL"] / [pyautogui_code]
            - sections: 解析出的各段内容字典（thought, action, code, original_code, answer 等）
    """
    logger.info(f"Response: {response}")
    input_string = response['content'].lstrip()

    sections = {}
    try:
        if thinking:
            thought = response.get('reasoning_content', '').strip()
            sections['thought'] = thought
            logger.info(f"Extracted thought (thinking): {sections['thought'][:200]}")
            # 移除 ## Action 之前的多余内容
            m = re.search(r"^##\s*Action\b", input_string, flags=re.MULTILINE)
            if m:
                input_string = input_string[m.start():]
        else:
            thought = re.search(
                r'^##\s*Thought\s*:?[\n\r]+(.*?)(?=^##\s*Action:|^##|\Z)',
                input_string, re.DOTALL | re.MULTILINE
            )
            if thought:
                sections['thought'] = thought.group(1).strip()
            else:
                sections['thought'] = ""

            logger.info(f"Extracted thought (non-thinking): {sections['thought'][:200]}")

        # 提取 Action 段
        action_match = re.search(
            r'^\s*##\s*Action\s*:?\s*[\n\r]+(.*?)(?=^\s*##|\Z)',
            input_string, re.DOTALL | re.MULTILINE
        )
        if action_match:
            action = action_match.group(1).strip()
            sections['action'] = action.strip()

        # 提取代码块
        code_blocks = re.findall(
            r'```(?:code|python)?\s*(.*?)\s*```',
            input_string, re.DOTALL | re.IGNORECASE
        )
        if not code_blocks:
            logger.error("No code blocks found in the input string")
            return f"<Error>: no code blocks found in the input string: {input_string}", ["FAIL"], sections

        code_block = code_blocks[-1].strip()
        sections['original_code'] = code_block

        # 检查特殊动作：computer.wait
        if "computer.wait" in code_block.lower():
            sections["code"] = "WAIT"
            return sections.get('action', ''), ["WAIT"], sections

        # 检查特殊动作：computer.terminate
        elif "computer.terminate" in code_block.lower():
            lower_block = code_block.lower()
            # 提取 answer 字段（用于 QA 任务）
            answer = _extract_answer_from_terminate(code_block)
            if answer:
                sections['answer'] = answer

            if ("failure" in lower_block) or ("fail" in lower_block):
                sections['code'] = "FAIL"
                return code_block, ["FAIL"], sections
            elif "success" in lower_block:
                sections['code'] = "DONE"
                return code_block, ["DONE"], sections
            else:
                logger.error("Terminate action found but no specific status provided in code block")
                return f"<Error>: terminate action found but no specific status provided in code block: {input_string}", ["FAIL"], sections

        # 正常 pyautogui 代码：进行坐标转换
        corrected_code = code_block
        sections['code'] = corrected_code
        sections['code'] = project_coordinate_to_absolute_scale(
            corrected_code,
            screen_width=screen_size[0],
            screen_height=screen_size[1],
            coordinate_type=coordinate_type
        )

        if ('code' not in sections or sections['code'] is None or sections['code'] == "") or \
           ('action' not in sections or sections['action'] is None or sections['action'] == ""):
            logger.error("Missing required action or code section")
            return f"<Error>: no code parsed: {input_string}", ["FAIL"], sections

        return sections['action'], [sections['code']], sections

    except Exception as e:
        error_message = f"<Error>: parsing response: {str(e)}\nTraceback:\n{traceback.format_exc()}\nInput string: {input_string}"
        logger.exception(error_message)
        return error_message, ['FAIL'], sections


def _extract_answer_from_terminate(code_block: str) -> Optional[str]:
    """
    从 computer.terminate 代码块中提取 answer 字段（用于 QA 任务）

    支持的格式示例：
        computer.terminate(status="success", answer="42")
        computer.terminate(status='success', answer='The answer is 42')

    Args:
        code_block: 包含 computer.terminate 调用的代码字符串

    Returns:
        Optional[str]: 提取到的 answer 字符串，未找到则返回 None
    """
    # 尝试匹配 answer="..." 或 answer='...'
    answer_match = re.search(
        r'answer\s*=\s*["\'](.+?)["\']',
        code_block, re.DOTALL
    )
    if answer_match:
        return answer_match.group(1).strip()
    return None


# ============================================================
# 坐标转换函数（来自 OSWorld 官方 KimiAgent）
# ============================================================

def project_coordinate_to_absolute_scale(
    pyautogui_code_relative_coordinates: str,
    screen_width: int,
    screen_height: int,
    coordinate_type: str = "relative"
) -> str:
    """
    将 pyautogui 代码中的相对坐标转换为绝对像素坐标

    转换逻辑：
    - 如果 x <= 1.0 且 y <= 1.0，视为 0~1 范围的相对坐标，乘以屏幕尺寸
    - 否则视为已经是绝对坐标，直接取整

    Args:
        pyautogui_code_relative_coordinates: 包含相对坐标的 pyautogui 代码字符串
        screen_width: 屏幕宽度（像素）
        screen_height: 屏幕高度（像素）
        coordinate_type: 坐标类型 "relative" / "absolute" / "qwen25"

    Returns:
        str: 坐标转换后的 pyautogui 代码字符串
    """
    def _coordinate_projection(x, y, screen_width, screen_height, coordinate_type):
        """内部坐标投影函数"""
        if x <= 1.0 and y <= 1.0:
            return int(round(x * screen_width)), int(round(y * screen_height))
        else:
            return int(round(x)), int(round(y))

    pattern = r'(pyautogui\.\w+\([^\)]*\))'
    matches = re.findall(pattern, pyautogui_code_relative_coordinates)

    new_code = pyautogui_code_relative_coordinates

    for full_call in matches:
        func_name_pattern = r'(pyautogui\.\w+)\((.*)\)'
        func_match = re.match(func_name_pattern, full_call, re.DOTALL)
        if not func_match:
            continue

        func_name = func_match.group(1)
        args_str = func_match.group(2)

        try:
            parsed_expr = ast.parse(f"func({args_str})").body[0]
            assert isinstance(parsed_expr, ast.Expr)
            parsed_call = parsed_expr.value
            assert isinstance(parsed_call, ast.Call)
            parsed_args = parsed_call.args
            parsed_keywords = parsed_call.keywords
        except (SyntaxError, AssertionError):
            return pyautogui_code_relative_coordinates

        # pyautogui 函数的参数名映射表
        function_parameters = {
            'click': ['x', 'y', 'clicks', 'interval', 'button', 'duration', 'pause'],
            'rightClick': ['x', 'y', 'duration', 'tween', 'pause'],
            'middleClick': ['x', 'y', 'duration', 'tween', 'pause'],
            'doubleClick': ['x', 'y', 'interval', 'button', 'duration', 'pause'],
            'tripleClick': ['x', 'y', 'interval', 'button', 'duration', 'pause'],
            'moveTo': ['x', 'y', 'duration', 'tween', 'pause'],
            'dragTo': ['x', 'y', 'duration', 'button', 'mouseDownUp', 'pause'],
        }

        func_base_name = func_name.split('.')[-1]
        param_names = function_parameters.get(func_base_name, [])

        args = {}
        for idx, arg in enumerate(parsed_args):
            if idx < len(param_names):
                param_name = param_names[idx]
                arg_value = ast.literal_eval(arg)
                args[param_name] = arg_value

        try:
            for kw in parsed_keywords:
                param_name = kw.arg
                arg_value = ast.literal_eval(kw.value)
                args[param_name] = arg_value
        except Exception as e:
            logger.error(f"Error parsing keyword arguments: {e}")
            return pyautogui_code_relative_coordinates

        updated = False
        if 'x' in args and 'y' in args:
            try:
                x_rel = float(args['x'])
                y_rel = float(args['y'])
                x_abs, y_abs = _coordinate_projection(
                    x_rel, y_rel, screen_width, screen_height, coordinate_type
                )
                args['x'] = x_abs
                args['y'] = y_abs
                updated = True
            except ValueError:
                pass

        if updated:
            reconstructed_args = []
            for idx, param_name in enumerate(param_names):
                if param_name in args:
                    arg_value = args[param_name]
                    if isinstance(arg_value, str):
                        arg_repr = f"'{arg_value}'"
                    else:
                        arg_repr = str(arg_value)
                    reconstructed_args.append(arg_repr)
                else:
                    break

            used_params = set(param_names[:len(reconstructed_args)])
            for kw in parsed_keywords:
                if kw.arg not in used_params:
                    arg_value = args[kw.arg]
                    if isinstance(arg_value, str):
                        arg_repr = f"{kw.arg}='{arg_value}'"
                    else:
                        arg_repr = f"{kw.arg}={arg_value}"
                    reconstructed_args.append(arg_repr)

            new_args_str = ', '.join(reconstructed_args)
            new_full_call = f"{func_name}({new_args_str})"
            new_code = new_code.replace(full_call, new_full_call)

    return new_code


# ============================================================
# KimiGUIAgent 核心类
# ============================================================

class KimiGUIAgent:
    """
    基于 OSWorld 官方 KimiAgent 适配的 GUI Agent

    核心特点：
    - 使用 Kimi K2.5 专用 Prompt（Thought → Action → Code 三段式）
    - 支持 thinking（reasoning_content）/ non-thinking 两种模式
    - 通过 httpx 直接调用 Moonshot/DeerAPI 的 OpenAI 兼容接口
    - 支持外部注入 PythonController 用于截图获取和动作执行

    与官方 KimiAgent 的差异：
    - API 地址和密钥参数化（支持 moonshot 直连和 deerapi 代理）
    - 新增 controller 集成（截图获取、动作执行）
    - 新增 last_round_timing（与 ExecutionRecorder 兼容的计时信息）
    - 新增 answer 提取（QA 任务从 computer.terminate 中解析 answer）
    """

    def __init__(
        self,
        model: str = "kimi-k2.5",
        max_steps: int = 15,
        max_image_history_length: int = 3,
        platform: str = "ubuntu",
        max_tokens: int = 4096,
        top_p: float = 0.95,
        temperature: float = 1,
        action_space: str = "pyautogui",
        observation_type: str = "screenshot",
        screen_size: Tuple[int, int] = (1920, 1080),
        coordinate_type: str = "relative",
        password: str = "password",
        thinking: bool = True,
        api_key: Optional[str] = None,
        base_url: str = "https://api.moonshot.ai/v1",
        controller: Any = None,
        execute_actions: bool = True,
    ):
        """
        初始化 KimiGUIAgent

        Args:
            model: Kimi 模型名称，如 "kimi-k2.5"
            max_steps: 最大执行步数
            max_image_history_length: 历史截图最大保留数量（默认 3）
            platform: 操作系统平台 "ubuntu" / "windows"
            max_tokens: 模型最大输出 token 数
            top_p: 采样参数
            temperature: 温度参数
            action_space: 动作空间（仅支持 "pyautogui"）
            observation_type: 观测类型（仅支持 "screenshot"）
            screen_size: 屏幕分辨率 (width, height)
            coordinate_type: 坐标类型 "relative" / "absolute" / "qwen25"
            password: VM 的 sudo 密码
            thinking: 是否启用 thinking 模式（默认 True）
            api_key: API 密钥（若为 None，从环境变量 KIMI_API_KEY 读取）
            base_url: API 基础 URL（支持 moonshot 直连或 deerapi 代理）
            controller: 外部注入的 PythonController 实例
            execute_actions: 是否在 predict() 内部执行动作（默认 True）
        """
        assert coordinate_type in ["relative", "absolute", "qwen25"], f"Invalid coordinate_type: {coordinate_type}"
        assert action_space in ["pyautogui"], "Invalid action space"
        assert observation_type in ["screenshot"], "Invalid observation type"
        assert model is not None, "Model cannot be None"

        self.model = model
        self.platform = platform
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.action_space = action_space
        self.observation_type = observation_type
        self.coordinate_type = coordinate_type
        self.screen_size = screen_size
        self.max_image_history_length = max_image_history_length
        self.max_steps = max_steps
        self.password = password
        self.thinking = thinking

        # API 配置（参数化，支持 moonshot 直连和 deerapi 代理）
        self.api_key = api_key or os.environ.get('KIMI_API_KEY', '')
        self.base_url = base_url.rstrip('/')

        # 项目新增：controller 和动作执行
        self.controller = controller
        self.execute_actions = execute_actions

        # 根据 thinking 模式选择 prompt
        if self.thinking:
            self.system_prompt = SYSTEM_PROMPT_THINKING.replace("{password}", self.password)
            self.history_template = THOUGHT_HISTORY_TEMPLATE_THINKING
        else:
            self.system_prompt = SYSTEM_PROMPT_NON_THINKING.replace("{password}", self.password)
            self.history_template = THOUGHT_HISTORY_TEMPLATE_NON_THINKING

        # 历史状态管理
        self.actions = []
        self.observations = []
        self.cots = []

        # 项目新增：每轮计时信息（与 ExecutionRecorder 兼容）
        self.last_round_timing = None
        # 每轮 token 消耗（call_llm 调用后更新）
        self.last_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 调试用：保存最近一次 API 请求和响应的完整内容
        self.last_api_request = None   # dict: {model, messages, max_tokens, ...}
        self.last_api_response = None  # dict: API 原始返回的 message

        # 尝试从 controller 获取真实屏幕分辨率
        if self.controller:
            try:
                real_size = self.controller.get_vm_screen_size()
                if real_size and len(real_size) == 2 and real_size[0] > 0 and real_size[1] > 0:
                    self.screen_size = tuple(real_size)
                    logger.info(f"[KimiGUIAgent] 从 controller 获取屏幕分辨率: {self.screen_size}")
            except Exception as e:
                logger.warning(f"[KimiGUIAgent] 无法从 controller 获取屏幕分辨率，使用默认值 {self.screen_size}: {e}")

        logger.info(f"[KimiGUIAgent] 初始化完成: model={self.model}, thinking={self.thinking}, "
                     f"screen_size={self.screen_size}, base_url={self.base_url}")

    def reset(self):
        """
        重置 Agent 状态（清空历史记录）

        用于在新任务开始前清理上一次的执行记录
        """
        self.observations = []
        self.cots = []
        self.actions = []
        self.last_round_timing = None

    def _scale_scroll_for_windows(self, code: str, factor: int = 50) -> str:
        """
        Windows 平台下缩放 pyautogui.scroll 的滚动量

        pyautogui.scroll 在 Ubuntu 和 Windows 上的滚动单位不同，
        Windows 需要乘以 factor 倍才能达到相同效果

        Args:
            code: pyautogui 代码字符串
            factor: 缩放倍数（默认 50）

        Returns:
            str: 缩放后的代码字符串
        """
        if self.platform.lower() != "windows":
            return code

        pattern_pos = re.compile(r'(pyautogui\.scroll\()\s*([-+]?\d+)\s*\)')
        code = pattern_pos.sub(lambda m: f"{m.group(1)}{int(m.group(2)) * factor})", code)
        return code

    def predict(self, instruction: str, obs: Dict, **kwargs) -> Tuple[Any, List[str], Dict]:
        """
        基于当前观测预测下一步动作

        核心流程：
        1. 构建消息（system prompt + 历史交互 + 当前截图）
        2. 调用 Kimi API（带重试机制）
        3. 解析响应（Thought → Action → Code 三段式）
        4. 坐标转换（相对坐标 → 绝对像素坐标）
        5. 可选：通过 controller 执行动作

        Args:
            instruction: 任务指令文本
            obs: 观测字典，必须包含 {"screenshot": bytes}
            **kwargs: 可选参数
                - step_idx: 当前步骤索引（用于日志）

        Returns:
            Tuple[Any, List[str], Dict]:
                - response: 模型返回的原始 response 字典
                - pyautogui_actions: 动作代码列表
                    - ["DONE"]: 任务成功完成
                    - ["WAIT"]: 需要等待
                    - ["FAIL"]: 任务失败
                    - [pyautogui_code]: 可执行的 pyautogui 代码
                - sections: 解析出的各段内容（thought, action, code, answer 等）
        """
        step_idx = kwargs.get('step_idx', len(self.actions) + 1)
        logger.info("[TRACE][KimiGUIAgent] ========= predict() Step %d 入口 =======", step_idx)
        logger.info("[TRACE][KimiGUIAgent] Instruction: %s", instruction[:200])
        logger.info("[TRACE][KimiGUIAgent] 历史步数=%d, 截图历史窗口=%d",
                    len(self.actions), self.max_image_history_length)

        think_start = time.time()

        # ---- 构建消息列表 ----
        messages = []
        messages.append({
            "role": "system",
            "content": self.system_prompt
        })
        instruction_prompt = INSTRUCTION_TEMPLATE.format(instruction=instruction)

        # 构建历史消息（保留最近 max_image_history_length 张截图）
        history_step_texts = []
        for i in range(len(self.actions)):
            if i > len(self.actions) - self.max_image_history_length:
                # 在历史窗口内：附带截图
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encode_image(self.observations[i]['screenshot'])}"
                            }
                        }
                    ]
                })

                history_content = STEP_TEMPLATE.format(step_num=i + 1) + self.history_template.format(
                    thought=self.cots[i].get('thought', ''),
                    action=self.cots[i].get('action', '')
                )

                messages.append({
                    "role": "assistant",
                    "content": history_content
                })
            else:
                # 超出窗口：只保留文本
                history_content = STEP_TEMPLATE.format(step_num=i + 1) + self.history_template.format(
                    thought=self.cots[i].get('thought', ''),
                    action=self.cots[i].get('action', '')
                )
                history_step_texts.append(history_content)
                if i == len(self.actions) - self.max_image_history_length:
                    messages.append({
                        "role": "assistant",
                        "content": "\n".join(history_step_texts)
                    })

        # 当前轮次的截图和指令
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{encode_image(obs['screenshot'])}"
                    }
                },
                {
                    "type": "text",
                    "text": instruction_prompt
                }
            ]
        })

        # ---- 调用 API（带重试） ----
        # 重试逻辑集中在此层，call_llm() 不做内部重试
        msg_count = len(messages)
        # 估算消息大小（不含图片内容）
        msg_text_len = sum(len(str(m.get("content", "")))
                          for m in messages if isinstance(m.get("content"), str))
        logger.info("[TRACE][KimiGUIAgent] 消息构建完成: %d 条消息, 文本总长=%d chars (%.2fs)",
                    msg_count, msg_text_len, time.time() - think_start)
        max_retry = 3
        retry_count = 0
        response: Optional[Dict] = None
        low_level_instruction: Optional[str] = None
        pyautogui_actions: List[str] = []
        other_cot: Dict = {}

        while retry_count < max_retry:
            try:
                api_payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "top_p": self.top_p,
                    "temperature": self.temperature if retry_count == 0 else max(0.2, self.temperature)
                }
                # 保存完整的 API 请求（供调试/日志记录）
                self.last_api_request = api_payload

                logger.info("[KimiGUIAgent] predict() 调用 call_llm (retry %d/%d)...",
                            retry_count + 1, max_retry)
                response = self.call_llm(api_payload)

                # 保存原始响应（供调试/日志记录）
                self.last_api_response = response

                logger.info(f"Model Output: {str(response)[:500]}")
                if not response:
                    logger.error("No response found in the response.")
                    raise ValueError(f"No response found in the response: {response}")

                low_level_instruction, pyautogui_actions, other_cot = parse_response_to_cot_and_action(
                    response, self.screen_size, self.coordinate_type, thinking=self.thinking
                )
                if "<Error>" in str(low_level_instruction) or not pyautogui_actions:
                    logger.error(f"Error parsing response: {low_level_instruction}")
                    raise ValueError(f"Error parsing response: {low_level_instruction}")
                break

            except Exception as e:
                logger.error(f"Error during prediction (retry {retry_count + 1}/{max_retry}): {e}")
                retry_count += 1
                if retry_count == max_retry:
                    logger.error("Maximum retries reached. Exiting.")
                    think_end = time.time()
                    self.last_round_timing = {
                        "think_start": think_start,
                        "think_end": think_end,
                        "action_start": think_end,
                        "action_end": think_end,
                    }
                    return str(e), ['FAIL'], other_cot
                # 重试前等待，避免快速重试冲击 API
                logger.info("[KimiGUIAgent] 等待 10s 后重试...")
                time.sleep(10)

        think_end = time.time()

        # 防御性检查：正常流程不会到达这里时 pyautogui_actions 仍为空
        if not pyautogui_actions:
            self.last_round_timing = {
                "think_start": think_start, "think_end": think_end,
                "action_start": think_end, "action_end": think_end,
            }
            return response, ['FAIL'], other_cot

        # ---- Windows 滚动适配 ----
        pyautogui_actions = [
            self._scale_scroll_for_windows(code) for code in pyautogui_actions
        ]
        logger.info(f"Action: {low_level_instruction}")
        logger.info(f"Code: {pyautogui_actions}")

        # ---- 更新历史状态 ----
        self.observations.append(obs)
        self.actions.append(low_level_instruction)
        self.cots.append(other_cot)

        # ---- 最大步数检查 ----
        current_step = len(self.actions)
        if current_step >= self.max_steps and pyautogui_actions and \
           'computer.terminate' not in pyautogui_actions[0].lower():
            logger.warning(f"Reached maximum steps {self.max_steps}. Forcing termination.")
            low_level_instruction = 'Fail the task because reaching the maximum step limit.'
            pyautogui_actions = ['FAIL']
            other_cot['code'] = 'FAIL'

        # ---- 动作执行（可选） ----
        action_start = time.time()
        if self.execute_actions and self.controller and pyautogui_actions:
            for idx, action_code in enumerate(pyautogui_actions):
                if action_code in ["DONE", "WAIT", "FAIL"]:
                    logger.info("[TRACE][KimiGUIAgent] 特殊动作: %s", action_code)
                    continue
                try:
                    logger.info("[TRACE][KimiGUIAgent] 执行 pyautogui 代码 [%d]: %s", idx, action_code[:200])
                    t0 = time.time()
                    self.controller.execute_python_command(action_code)
                    logger.info("[TRACE][KimiGUIAgent] 动作执行完成 (%.2fs)", time.time() - t0)
                except Exception as e:
                    logger.error("[TRACE][KimiGUIAgent] 动作执行失败: %s", e)
        action_end = time.time()
        logger.info("[TRACE][KimiGUIAgent] predict() 完成: think=%.2fs, action=%.2fs, 总=%.2fs",
                    think_end - think_start, action_end - action_start, action_end - think_start)

        # ---- 记录计时信息 ----
        self.last_round_timing = {
            "think_start": think_start,
            "think_end": think_end,
            "action_start": action_start,
            "action_end": action_end,
        }

        return response, pyautogui_actions, other_cot

    def call_llm(self, payload: Dict) -> Optional[Dict]:
        """
        调用 Kimi LLM API（流式模式，单次调用无内部重试）

        使用 OpenAI SDK 的流式接口调用 Moonshot/DeerAPI 的 chat/completions 端点。
        不做内部重试——重试逻辑统一由上层 predict() 管理，避免双重重试导致等待时间爆炸
        （旧版 5×5=25 次重试 × 超时 = 数小时的"假性挂起"）。

        输入:
            payload: 请求体字典，包含 model, messages, max_tokens, top_p, temperature

        输出:
            Optional[Dict]: 模型返回的 message 字典（包含 content 和可选的 reasoning_content），
                           失败时直接抛出异常（由 predict() 捕获并重试）
        """
        import json as _json
        from openai import OpenAI as _OpenAI
        import httpx as _httpx

        # ---- 请求级诊断日志 ----
        # 统计 payload 大小：消息数、图片数、各图片 base64 长度、纯文本长度
        msgs = payload.get("messages", [])
        num_images = 0
        total_image_b64_bytes = 0
        total_text_chars = 0
        for m in msgs:
            c = m.get("content")
            if isinstance(c, str):
                total_text_chars += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            total_text_chars += len(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            num_images += 1
                            url = part.get("image_url", {}).get("url", "")
                            # data:image/png;base64,<data> 的 <data> 部分
                            if url.startswith("data:"):
                                b64_data = url.split(",", 1)[-1] if "," in url else ""
                                total_image_b64_bytes += len(b64_data)

        # 估算实际 JSON payload 大小
        try:
            raw_payload_size = len(_json.dumps({
                "model": payload["model"],
                "messages": payload["messages"],
                "max_tokens": payload.get("max_tokens", 4096),
                "stream": True,
            }, ensure_ascii=False))
        except Exception:
            raw_payload_size = -1

        logger.info("[TRACE][KimiGUIAgent] ===== API 请求诊断 =====")
        logger.info("[TRACE][KimiGUIAgent]   model: %s", payload["model"])
        logger.info("[TRACE][KimiGUIAgent]   base_url: %s", self.base_url)
        logger.info("[TRACE][KimiGUIAgent]   消息数: %d, 图片数: %d", len(msgs), num_images)
        logger.info("[TRACE][KimiGUIAgent]   图片 base64 总大小: %.2f MB", total_image_b64_bytes / 1024 / 1024)
        logger.info("[TRACE][KimiGUIAgent]   纯文本总长: %d chars", total_text_chars)
        logger.info("[TRACE][KimiGUIAgent]   JSON payload 估算: %.2f MB", raw_payload_size / 1024 / 1024 if raw_payload_size > 0 else -1)
        logger.info("[TRACE][KimiGUIAgent]   参数: max_tokens=%s, top_p=%s, temperature=%s",
                    payload.get("max_tokens"), payload.get("top_p"), payload.get("temperature"))

        # ---- 发送请求 ----
        # 连接超时 30s，读取超时 120s（thinking 模型首 token 可能较慢，但 2 分钟足够）
        api_timeout = _httpx.Timeout(120.0, connect=30.0)

        client = _OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=api_timeout,
            max_retries=0,  # 禁用 SDK 内部重试，由上层 predict() 统一管理
        )

        t_request_start = time.time()
        logger.info("[TRACE][KimiGUIAgent] >>> HTTP 请求发送 (POST %s/chat/completions)...", self.base_url)

        from parallel_benchmark.utils.llm_determinism import (
            LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
        )
        _kimi_kwargs = dict(
            model=payload["model"],
            messages=payload["messages"],
            max_tokens=payload.get("max_tokens", 4096),
            top_p=payload.get("top_p", 0.7),
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
            stream=True,
            stream_options={"include_usage": True},
        )
        assert_deterministic(_kimi_kwargs)
        stream = client.chat.completions.create(**_kimi_kwargs)

        t_stream_created = time.time()
        logger.info("[TRACE][KimiGUIAgent] <<< 流式连接已建立 (%.2fs)", t_stream_created - t_request_start)

        content = ""
        reasoning_content = ""
        finish_reason = None
        chunk_count = 0

        for chunk in stream:
            chunk_count += 1
            if chunk_count == 1:
                t_first_chunk = time.time()
                logger.info("[TRACE][KimiGUIAgent] 首个 chunk 到达 (连接后 %.2fs, 总 %.2fs)",
                            t_first_chunk - t_stream_created, t_first_chunk - t_request_start)
            if chunk.usage:
                self.last_token_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                reasoning_content += delta.reasoning_content
            if delta.content:
                content += delta.content

        t_stream_end = time.time()
        logger.info("[TRACE][KimiGUIAgent] 流式接收完成: %d chunks, finish_reason=%s, "
                    "content=%d chars, reasoning=%d chars, 总耗时=%.2fs",
                    chunk_count, finish_reason, len(content), len(reasoning_content),
                    t_stream_end - t_request_start)

        if finish_reason == "stop":
            msg = {"role": "assistant", "content": content}
            if reasoning_content:
                msg["reasoning_content"] = reasoning_content
            return msg
        else:
            raise ValueError(f"LLM 未正常完成 (finish_reason={finish_reason}, "
                           f"chunks={chunk_count}, content_len={len(content)})")
