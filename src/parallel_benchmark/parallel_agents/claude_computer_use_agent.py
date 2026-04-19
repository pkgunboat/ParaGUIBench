"""
Claude Computer Use Agent
使用 Claude Sonnet 4.5 的 Computer Use API 实现的 GUI Agent
通过 PythonController 控制远程虚拟机
"""

import os
import sys
import time
import base64
import json
import logging
from io import BytesIO
from typing import Dict, List, Tuple, Optional

import requests
from PIL import Image

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(current_dir, '../..'))

from desktop_env.controllers.python import PythonController
from desktop_env.actions import KEYBOARD_KEYS

# Claude prompt 定义（始终可用）
from parallel_benchmark.prompts.claude_computer_use import (
    get_claude_system_prompt,
    CLAUDE_USER_PROMPT_FIRST,
    CLAUDE_USER_PROMPT_CONTINUE,
)

# benchmarkClient cookbook（gpt_computer_use.get_computer_use_tool）不随开源版分发；
# 若需要启用 Claude Computer Use agent，请自行安装 benchmarkClient 后 import。
try:
    from benchmarkClient.cookbooks.gpt.gpt_computer_use import (
        get_computer_use_tool,
    )
except ImportError:  # pragma: no cover
    def get_computer_use_tool(*args, **kwargs):
        raise NotImplementedError(
            "ClaudeComputerUseAgent 依赖外部 benchmarkClient.cookbooks.gpt.gpt_computer_use，"
            "请单独安装该模块或改用其它 GUI Agent（seed18/gpt54/kimi）。"
        )

# 使用 Claude 专用的 prompt
SYSTEM_PROMPT = get_claude_system_prompt()
USER_PROMPT_FIRST = CLAUDE_USER_PROMPT_FIRST
USER_PROMPT_CONTINUE = CLAUDE_USER_PROMPT_CONTINUE

logger = logging.getLogger(__name__)


class ClaudeComputerUseAgent:
    """
    Claude Computer Use Agent
    
    使用 Claude Sonnet 4.5 的 Computer Use API，通过 PythonController 控制远程虚拟机
    """
    
    def __init__(
        self,
        vm_ip: str = "127.0.0.1",
        vm_port: int = 5001,
        api_key: str = None,
        base_url: str = "https://api.deerapi.com/v1/",
        model_name: str = "claude-sonnet-4-5-20250929",
        max_trajectory_length: int = 20,
        screenshot_compression: bool = True,
        max_screenshot_size: int = 1280,
        max_recent_images: int = 3,
        runtime_conf: dict = None
    ):
        """
        初始化 Claude Computer Use Agent

        Args:
            vm_ip: 虚拟机 IP
            vm_port: 虚拟机服务端口
            api_key: Claude API 密钥
            base_url: API 基础 URL
            model_name: 模型名称
            max_trajectory_length: 最大轨迹长度
            screenshot_compression: 是否压缩截图
            max_screenshot_size: 截图最大边长
            max_recent_images: 保留最近 N 张截图（0 或 None 表示不过滤）
            runtime_conf: 运行时配置
        """
        # 虚拟机配置
        self.vm_ip = vm_ip
        self.vm_port = vm_port
        self.controller = PythonController(vm_ip=vm_ip, server_port=vm_port)
        
        # API 配置
        self.api_key = api_key or os.getenv("CLAUDE_API_KEY", "${OPENAI_API_KEY}")
        self.base_url = base_url
        self.model_name = model_name
        
        # 运行时配置
        self.runtime_conf = runtime_conf or {}
        self.max_trajectory_length = max_trajectory_length
        self.screenshot_compression = screenshot_compression
        self.max_screenshot_size = max_screenshot_size
        self.max_recent_images = max_recent_images
        self.temperature = self.runtime_conf.get("temperature", 0.0)
        self.max_tokens = self.runtime_conf.get("max_tokens", 2000)
        self.max_retries = self.runtime_conf.get("max_retries", 3)
        self.timeout = self.runtime_conf.get("timeout", 300)
        
        # 获取屏幕信息
        try:
            screen_size = self.controller.get_vm_screen_size()
            if screen_size:
                self.screen_width = screen_size.get("width", 1920)
                self.screen_height = screen_size.get("height", 1080)
            else:
                self.screen_width = 1920
                self.screen_height = 1080
            logger.info(f"Screen size: {self.screen_width}x{self.screen_height}")
        except Exception as e:
            logger.warning(f"Failed to get screen size: {e}, using default 1920x1080")
            self.screen_width = 1920
            self.screen_height = 1080
        
        # 压缩后的分辨率和缩放比例
        self.compressed_width = self.screen_width
        self.compressed_height = self.screen_height
        self.scale_x = 1.0
        self.scale_y = 1.0
        
        # 获取工具定义（初始使用真实分辨率，首次截图后会更新）
        self.tools = [
            get_computer_use_tool(self.screen_width, self.screen_height),
        ]
        
        # 初始化历史记录
        self.reset()
        
        # 记录每轮的详细信息（messages和screenshot）
        self.round_details = []
        
        # 性能统计
        self.last_inference_time = None
        
        # Token usage 追踪（累计该 Agent 所有 API 调用的 token 消耗）
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 最近一次 API 请求/响应记录（用于调试和日志）
        self.last_api_request = None
        self.last_api_response = None
    
    def reset(self):
        """重置 Agent 状态"""
        self.messages = []
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_screenshots = []
        self.turn_count = 0
        self.round_details = []  # 清空轮次详细信息
        logger.info("Agent state reset")
    
    def take_screenshot(self) -> str:
        """
        从远程虚拟机获取截图并返回 base64 编码
        
        Returns:
            str: base64 编码的截图
        """
        screenshot_bytes = self.controller.get_screenshot()
        
        if screenshot_bytes is None:
            raise RuntimeError("Failed to get screenshot from VM")
        
        # 压缩截图
        if self.screenshot_compression:
            image = Image.open(BytesIO(screenshot_bytes))
            original_width, original_height = image.size
            
            # 调整大小
            if max(image.size) > self.max_screenshot_size:
                ratio = self.max_screenshot_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
                
                # 更新压缩后的分辨率和缩放比例
                self.compressed_width, self.compressed_height = new_size
                self.scale_x = original_width / self.compressed_width
                self.scale_y = original_height / self.compressed_height
                
                # 更新工具定义（使用压缩后的分辨率）
                self.tools = [
                    get_computer_use_tool(self.compressed_width, self.compressed_height),
                ]
                
                logger.debug(f"Screenshot resized: {original_width}x{original_height} → {new_size}")
                logger.debug(f"Coordinate scale: X={self.scale_x:.2f}, Y={self.scale_y:.2f}")
            else:
                self.compressed_width, self.compressed_height = original_width, original_height
                self.scale_x, self.scale_y = 1.0, 1.0
            
            # 转换为 JPEG
            output = BytesIO()
            image.convert('RGB').save(output, format='JPEG', quality=85, optimize=True)
            compressed_bytes = output.getvalue()
            
            logger.debug(f"Screenshot compressed: {len(screenshot_bytes)} → {len(compressed_bytes)} bytes")
            screenshot_bytes = compressed_bytes
        
        return base64.b64encode(screenshot_bytes).decode('utf-8')
    
    def _filter_to_recent_images(self) -> None:
        """
        过滤 self.messages 中的历史截图，仅保留最近 max_recent_images 张。

        对于被移除截图的 user 消息，保留文本内容（instruction prompt），仅删除 image_url 块。
        这样模型仍能看到历史对话的文本上下文，但不会因大量图片导致 token 爆炸。

        输入: 无（直接操作 self.messages）
        输出: 无（原地修改 self.messages）
        """
        if not self.max_recent_images or self.max_recent_images <= 0:
            return

        # 从后向前收集所有包含图片的 user 消息索引
        image_msg_indices = []
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            has_image = any(
                isinstance(block, dict) and block.get("type") == "image_url"
                for block in content
            )
            if has_image:
                image_msg_indices.append(i)

        # image_msg_indices 已按从新到旧排列；仅需处理超出限制的部分
        if len(image_msg_indices) <= self.max_recent_images:
            return

        indices_to_strip = image_msg_indices[self.max_recent_images:]
        for idx in indices_to_strip:
            content = self.messages[idx]["content"]
            # 保留非图片块（text 等），移除 image_url 块
            new_content = [
                block for block in content
                if not (isinstance(block, dict) and block.get("type") == "image_url")
            ]
            if new_content:
                self.messages[idx]["content"] = new_content
            else:
                self.messages[idx]["content"] = [{"type": "text", "text": "[screenshot removed]"}]

        logger.debug(
            "Filtered %d old screenshots, keeping %d most recent",
            len(indices_to_strip), self.max_recent_images,
        )

    def call_api(self, messages: List[Dict]) -> Dict:
        """
        调用 Claude API (带重试机制)
        
        Args:
            messages: 消息列表
            
        Returns:
            dict: API 响应的 message 对象
        """
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "tools": self.tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        logger.info(f"Calling API: {self.model_name}, messages: {len(messages)}")

        # 保存完整的 API 请求（用于调试）
        self.last_api_request = {
            "url": url,
            "model": self.model_name,
            "messages": messages,
            "tools": self.tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                response.raise_for_status()

                data = response.json()

                # 保存完整的 API 响应（用于调试）
                self.last_api_response = data

                message = data["choices"][0]["message"]
                
                # 防御：某些 API 可能返回 message=null
                if message is None:
                    logger.warning("API returned message=null, using empty fallback")
                    message = {"role": "assistant", "content": "", "tool_calls": None}
                
                self.last_inference_time = time.time() - start_time
                logger.info(f"API call successful (took {self.last_inference_time:.2f}s)")
                
                # 提取并累计 token usage
                usage = data.get("usage", {})
                if usage:
                    self.token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
                    self.token_usage["completion_tokens"] += usage.get("completion_tokens", 0) or 0
                    self.token_usage["total_tokens"] += usage.get("total_tokens", 0) or 0
                    logger.info(f"Token usage this call: prompt={usage.get('prompt_tokens', 0)}, completion={usage.get('completion_tokens', 0)}, cumulative_total={self.token_usage['total_tokens']}")
                
                return message
                
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout (attempt {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    time.sleep(wait_time)
                else:
                    raise
                    
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [502, 503, 504]:
                    logger.warning(f"Gateway error {e.response.status_code} (attempt {attempt + 1}/{self.max_retries})")
                    if attempt < self.max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        time.sleep(wait_time)
                    else:
                        raise
                else:
                    logger.error(f"HTTP error: {e}, response: {e.response.text}")
                    raise
                    
            except Exception as e:
                logger.error(f"Request failed: {e}")
                raise
    
    def execute_computer_action(self, action_dict: Dict):
        """
        将 Claude Computer Use 动作转换为 PythonController 动作并执行
        坐标会自动缩放到真实屏幕分辨率
        
        Args:
            action_dict: Claude 动作字典
        """
        action = action_dict.get("action")
        logger.debug(f"Executing action: {action}")
        
        # 坐标缩放函数
        def scale_coordinate(x, y):
            """将压缩图上的坐标缩放到真实分辨率"""
            real_x = int(x * self.scale_x)
            real_y = int(y * self.scale_y)
            if self.scale_x != 1.0 or self.scale_y != 1.0:
                logger.debug(f"Coordinate scaled: ({x}, {y}) → ({real_x}, {real_y})")
            return real_x, real_y
        
        if action == "left_click":
            x, y = action_dict["coordinate"]
            real_x, real_y = scale_coordinate(x, y)
            self.controller.execute_action({
                "action_type": "CLICK",
                "parameters": {"button": "left", "x": real_x, "y": real_y}
            })
            
        elif action == "right_click":
            x, y = action_dict["coordinate"]
            real_x, real_y = scale_coordinate(x, y)
            self.controller.execute_action({
                "action_type": "RIGHT_CLICK",
                "parameters": {"x": real_x, "y": real_y}
            })
            
        elif action == "double_click":
            x, y = action_dict["coordinate"]
            real_x, real_y = scale_coordinate(x, y)
            self.controller.execute_action({
                "action_type": "DOUBLE_CLICK",
                "parameters": {"x": real_x, "y": real_y}
            })
        
        elif action == "triple_click":
            # 三击用于选中整行文本
            x, y = action_dict["coordinate"]
            real_x, real_y = scale_coordinate(x, y)
            # 连续点击三次
            for _ in range(3):
                self.controller.execute_action({
                    "action_type": "CLICK",
                    "parameters": {"button": "left", "x": real_x, "y": real_y}
                })
                time.sleep(0.1)
            
        elif action == "mouse_move":
            x, y = action_dict["coordinate"]
            real_x, real_y = scale_coordinate(x, y)
            self.controller.execute_action({
                "action_type": "MOVE_TO",
                "parameters": {"x": real_x, "y": real_y}
            })
            
        elif action == "type":
            text = action_dict.get("text", "")
            self.controller.execute_action({
                "action_type": "TYPING",
                "parameters": {"text": text}
            })
            
        elif action == "key":
            keys = action_dict.get("keys", [])
            
            # 键值映射：Claude 格式 -> PyAutoGUI 格式
            key_mapping = {
                "Return": "enter",
                "Enter": "enter",
                "ArrowUp": "up",
                "ArrowDown": "down",
                "ArrowLeft": "left",
                "ArrowRight": "right",
                "PageUp": "pageup",
                "PageDown": "pagedown",
                "Page_Up": "pageup",
                "Page_Down": "pagedown",
                "Home": "home",
                "End": "end",
                "Delete": "delete",
                "Backspace": "backspace",
                "Tab": "tab",
                "Escape": "esc",
                "Space": " ",
                "Control": "ctrl",
                "Alt": "alt",
                "Shift": "shift",
                "Meta": "win",
                "Command": "command",
                "plus": "+",
                "minus": "-",
                "equal": "=",
            }
            
            # 转换键值
            converted_keys = []
            for key in keys:
                # 如果在映射表中，使用映射值
                if key in key_mapping:
                    converted_keys.append(key_mapping[key])
                # 如果是单字符，直接使用（转小写）
                elif len(key) == 1:
                    converted_keys.append(key.lower())
                # 如果已经是 PyAutoGUI 格式的特殊键，直接使用
                elif key.lower() in KEYBOARD_KEYS:
                    converted_keys.append(key.lower())
                else:
                    # 不支持的键，记录警告并跳过
                    logger.warning(f"Unsupported key: {key}, skipping")
                    continue
            
            # 如果没有有效的键，直接返回
            if not converted_keys:
                logger.warning(f"No valid keys to press from: {keys}")
                return
            
            if len(converted_keys) == 1:
                self.controller.execute_action({
                    "action_type": "PRESS",
                    "parameters": {"key": converted_keys[0]}
                })
            else:
                self.controller.execute_action({
                    "action_type": "HOTKEY",
                    "parameters": {"keys": converted_keys}
                })
                
        elif action == "scroll":
            dx = action_dict.get("dx", 0)
            dy = action_dict.get("dy", 0)
            
            # 添加调试信息
            print(f"\n[DEBUG SCROLL] Received scroll action:")
            print(f"  action_dict = {action_dict}")
            print(f"  dx = {dx}, dy = {dy}")
            
            # 检查是否有其他scroll参数（Claude可能使用不同的格式）
            if "pixels" in action_dict:
                pixels = action_dict["pixels"]
                print(f"  pixels = {pixels} (found 'pixels' parameter)")
                # pixels: 正值向上滚动，负值向下滚动
                # 添加硬限制：scroll units应该很小（0-5），最大不超过10
                pixels = max(-10, min(10, pixels))  # 限制在 [-10, 10] 范围内
                print(f"  pixels after limit = {pixels}")
                dy = pixels  # 直接使用pixels值，不反转
                dx = 0
            
            if "scroll_direction" in action_dict and "scroll_amount" in action_dict:
                direction = action_dict["scroll_direction"]
                amount = action_dict["scroll_amount"]
                print(f"  scroll_direction = {direction}, scroll_amount = {amount}")
                # 添加硬限制：scroll units应该很小（0-5），最大不超过10
                amount = max(-10, min(10, abs(amount)))  # 限制在 [0, 10] 范围内
                print(f"  scroll_amount after limit = {amount}")
                # 将direction转换为dx/dy
                if direction == "down":
                    dy = amount  # 向下滚动
                elif direction == "up":
                    dy = -amount   # 向上滚动
                elif direction == "left":
                    dx = amount
                elif direction == "right":
                    dx = -amount
            
            print(f"  Final: dx = {dx}, dy = {dy}")
            print(f"  Executing: pyautogui.hscroll({dx}) and pyautogui.vscroll({dy})")
            
            self.controller.execute_action({
                "action_type": "SCROLL",
                "parameters": {"dx": dx, "dy": dy}
            })
            
        elif action == "wait":
            duration = action_dict.get("time", 1)
            time.sleep(duration)
            
        elif action == "terminate":
            logger.info(f"Terminate action: {action_dict.get('status', 'success')}")
            
        else:
            logger.warning(f"Unknown action: {action}")

    def predict(self, instruction: str, obs: Dict, last_action_after_obs: Dict = None, screenshot_time: float = 0.0) -> Tuple[str, List[str], str]:
        """
        预测下一个动作
        
        Args:
            instruction: 任务指令
            obs: 观察 (包含 screenshot)
            last_action_after_obs: 上一个动作
            screenshot_time: 截图生成耗时（秒），用于计入preparation_time
            
        Returns:
            tuple: (思考, 动作列表, pyautogui代码)
                - 如果任务完成，返回 ("思考", ["DONE"], "DONE")
                - 如果需要等待，返回 ("思考", ["WAIT"], "WAIT")
                - 如果失败，返回 ("错误", ["FAIL"], "FAIL")
        """
        # ========== Round开始 ==========
        round_start_time = time.time()
        
        self.turn_count += 1
        
        # 检查是否超过最大步数
        if self.turn_count > self.max_trajectory_length:
            logger.warning(f"Exceeded max trajectory length: {self.max_trajectory_length}")
            return "Max steps exceeded", ["FAIL"], "FAIL"
        
        # ========== 阶段1: 准备阶段（构建消息）==========
        preparation_start_time = time.time()
        
        # 保存观察
        screenshot_base64 = obs.get("screenshot")
        if not screenshot_base64:
            logger.error("No screenshot in observation")
            return "No screenshot", ["FAIL"], "FAIL"
        
        # 保存screenshot URL（这里使用round索引，实际可以是文件路径）
        screenshot_url = f"screenshot_round_{self.turn_count}.png"  # 占位符，可以保存实际文件路径
        
        self.observations.append(obs)
        self.history_screenshots.append(screenshot_base64)
        
        # 构建消息 (第一次调用时初始化)
        if not self.messages:
            # 系统提示
            self.messages.append({
                "role": "system",
                "content": SYSTEM_PROMPT
            })
            
            # 第一次用户消息 (带截图和任务)
            self.messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT_FIRST.format(instruction=instruction)
                    }
                ]
            })
        
        preparation_end_time = time.time()
        # preparation_time = 截图时间 + 构建消息时间
        message_preparation_time = preparation_end_time - preparation_start_time
        preparation_time = screenshot_time + message_preparation_time
        print(f"[TIMING] Preparation: {preparation_time:.3f}s (screenshot: {screenshot_time:.3f}s, message: {message_preparation_time:.3f}s)")

        
        # ========== 阶段2: API调用阶段 ==========
        # 过滤旧截图，仅保留最近 N 张，减少 token 消耗
        self._filter_to_recent_images()

        api_start_time = time.time()

        # 调用 API
        try:
            assistant_message = self.call_api(self.messages)
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return f"API error: {e}", ["FAIL"], "FAIL"
        
        api_end_time = time.time()
        api_call_time = api_end_time - api_start_time
        print(f"[TIMING] API Call: {api_call_time:.3f}s (including retries)")
        
        # ========== 阶段3: 解析和执行阶段 ==========
        parsing_start_time = time.time()
        
        # 保存助手消息
        self.messages.append(assistant_message)
        
        # 提取思考内容
        # 某些兼容API可能返回 content=None，这里做空值兜底，避免后续 upper() 报错
        thought = assistant_message.get("content", "") or ""
        self.thoughts.append(thought)
        
        # 检查工具调用
        tool_calls = assistant_message.get("tool_calls")
        
        if not tool_calls:
            # 没有工具调用，检查是否是 XML 格式的工具调用
            # 有时 deerapi 会把工具调用作为纯文本返回在 content 里
            if thought and ("<function_calls>" in thought or "<invoke" in thought):
                logger.info("Detected XML format tool call in content, attempting to parse...")
                try:
                    # 预处理 XML：修复常见的格式问题
                    import re
                    xml_content = thought
                    
                    # 1. 移除 </parameter> 后面的多余字符（如 ')'）
                    xml_content = re.sub(r'</parameter>\s*\)', '</parameter>', xml_content)
                    
                    # 2. 如果缺少闭合标签，尝试补全
                    if '<invoke name="computer">' in xml_content:
                        if '</invoke>' not in xml_content:
                            xml_content += '\n</invoke>'
                        if '</function_calls>' not in xml_content:
                            xml_content += '\n</function_calls>'
                    
                    logger.info(f"Preprocessed XML:\n{xml_content}")
                    print(f"[DEBUG] Preprocessed XML:\n{xml_content}")
                    
                    # 尝试使用 adapter 的解析功能
                    from utils.action_parser import parse_action_to_structure_output
                    
                    # 获取 max_pixels 和 min_pixels，使用默认值如果未配置
                    max_pixels = self.runtime_conf.get("max_pixels", 1920 * 1080)
                    min_pixels = self.runtime_conf.get("min_pixels", 640 * 480)
                    
                    print(f"[DEBUG] Calling parse_action_to_structure_output with:")
                    print(f"[DEBUG]   - compressed_height: {self.compressed_height}")
                    print(f"[DEBUG]   - compressed_width: {self.compressed_width}")
                    print(f"[DEBUG]   - max_pixels: {max_pixels}")
                    print(f"[DEBUG]   - min_pixels: {min_pixels}")
                    
                    parsed_actions = parse_action_to_structure_output(
                        xml_content,  # 使用预处理后的 XML
                        factor=1000,
                        origin_resized_height=self.compressed_height,
                        origin_resized_width=self.compressed_width,
                        model_type="claude",
                        max_pixels=max_pixels,
                        min_pixels=min_pixels
                    )
                    
                    print(f"[DEBUG] parse_action_to_structure_output returned: {parsed_actions}")
                    
                    if parsed_actions:
                        logger.info(f"Successfully parsed XML format actions: {parsed_actions}")
                        print(f"[DEBUG] Successfully parsed XML, generating pyautogui code...")
                        # 转换为执行代码
                        action_codes = []
                        for i, parsed_action in enumerate(parsed_actions):
                            print(f"[DEBUG] Processing parsed_action {i+1}: {parsed_action}")
                            if "action_type" in parsed_action:
                                action_type = parsed_action["action_type"]
                                if action_type == "finished":
                                    print(f"[DEBUG] Action type is 'finished', returning DONE")
                                    return thought, ["DONE"], "DONE"
                                elif action_type == "wait":
                                    print(f"[DEBUG] Action type is 'wait', returning WAIT")
                                    return thought, ["WAIT"], "WAIT"
                            
                            # 转换为 pyautogui 代码
                            from utils.action_parser import parsing_response_to_pyautogui_code
                            print(f"[DEBUG] Calling parsing_response_to_pyautogui_code with:")
                            print(f"[DEBUG]   - responses: {parsed_action}")
                            print(f"[DEBUG]   - image_height: {self.screen_height}")
                            print(f"[DEBUG]   - image_width: {self.screen_width}")
                            code = parsing_response_to_pyautogui_code(
                                responses=parsed_action,
                                image_height=self.screen_height,
                                image_width=self.screen_width
                            )
                            print(f"[DEBUG] Generated code: {code}")
                            if code:
                                action_codes.append(code)
                        
                        print(f"[DEBUG] Total action_codes: {len(action_codes)}")
                        if action_codes:
                            full_code = "\n".join(action_codes)
                            # 从代码中提取动作类型
                            actions = []
                            for action in parsed_actions:
                                if "action_type" in action:
                                    actions.append(action["action_type"])
                            
                            print(f"[DEBUG] Returning actions: {actions}, code length: {len(full_code)}")
                            logger.info(f"Generated code from XML: {full_code[:100]}...")
                            return thought, actions, full_code
                        else:
                            print(f"[DEBUG] No action_codes generated!")
                    else:
                        print(f"[DEBUG] parsed_actions is None or empty!")
                    
                except Exception as e:
                    logger.warning(f"Failed to parse XML format: {e}")
                    print(f"[ERROR] Exception in XML parsing: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 没有工具调用也没有有效的 XML，检查是否完成
            # thought 可能为空字符串，upper/lower 仍安全
            if "DONE" in thought.upper() or "完成" in thought or "finished" in thought.lower():
                logger.info("Task completed (no tool calls)")
                return thought, ["DONE"], "DONE"
            else:
                logger.warning("No tool calls, continuing...")
                return thought, ["WAIT"], "WAIT"
        
        # 执行工具调用
        actions_executed = []
        new_screenshot = None
        
        for tool_call in tool_calls:
            function_name = tool_call["function"]["name"]
            raw_args = tool_call["function"].get("arguments")
            if raw_args is None or (isinstance(raw_args, str) and raw_args.strip().lower() in ("", "null")):
                arguments = {}
            else:
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    arguments = parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            
            if function_name == "computer_use":
                action = arguments.get("action")
                
                # 检查是否是终止动作
                if action == "terminate":
                    status = arguments.get("status", "success")
                    logger.info(f"Task terminated with status: {status}")
                    return thought, ["DONE"], "DONE"
                
                # 检查是否是回答动作（返回提取的数据）
                if action == "answer":
                    answer_text = arguments.get("text", "")
                    logger.info(f"Task completed with answer: {answer_text[:100]}...")
                    # 将answer_text作为thought返回，这样plan_agent可以获取实际数据
                    return answer_text if answer_text else thought, ["DONE"], "DONE"
                
                # 执行动作
                try:
                    self.execute_computer_action(arguments)
                    actions_executed.append(action)
                    time.sleep(0.5)  # 等待动作完成
                    
                    # 获取新截图
                    new_screenshot = self.take_screenshot()
                    
                except Exception as e:
                    logger.error(f"Action execution failed: {e}")
                    return f"Execution error: {e}", ["FAIL"], "FAIL"
                
                # 添加工具结果（只包含状态，不包含截图）
                self.messages.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": "Action executed successfully. See the updated screenshot."
                })
            else:
                logger.warning(f"Unsupported tool call: {function_name}")
                actions_executed.append(function_name)
                self.messages.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": f"ERROR: Unsupported tool '{function_name}'. Only computer_use is available."
                })
        
        # 如果有新截图，添加用户消息（带截图）
        if new_screenshot:
            self.messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{new_screenshot}"
                        }
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT_CONTINUE.format(instruction=instruction)
                    }
                ]
            })
        
        # 保存动作
        self.actions.append(actions_executed)
        
        # 返回结果 (格式兼容 gui_agent.py)
        # pyautogui_code 在这里不适用，返回动作描述
        pyautogui_code = f"# Claude Computer Use: {', '.join(actions_executed)}"
        
        parsing_end_time = time.time()
        parsing_time = parsing_end_time - parsing_start_time
        print(f"[TIMING] Parsing & Execution: {parsing_time:.3f}s")
        
        # 总时间 = (parsing_end - round_start) + screenshot_time
        # 因为 screenshot_time 是在 round_start 之前发生的，需要加上
        total_time = (parsing_end_time - round_start_time) + screenshot_time
        print(f"[TIMING] Total Round: {total_time:.3f}s")
        print(f"[TIMING] Breakdown: Prep={preparation_time:.3f}s + API={api_call_time:.3f}s + Parse&Exec={parsing_time:.3f}s")
        
        # 保存本轮的详细信息（messages和screenshot_url）
        self.round_details.append({
            "round": self.turn_count,
            "messages": [msg.copy() if isinstance(msg, dict) else msg for msg in self.messages],  # 深拷贝
            "screenshot_url": screenshot_url,
            "thought": thought,
            "actions": actions_executed,
            "timing": {
                "preparation_time": preparation_time,
                "api_call_time": api_call_time,
                "parsing_and_execution_time": parsing_time,
                "total_round_time": total_time
            }
        })
        
        return thought, actions_executed, pyautogui_code


if __name__ == "__main__":
    # 简单测试
    logging.basicConfig(level=logging.INFO)
    
    agent = ClaudeComputerUseAgent(
        vm_ip="127.0.0.1",
        vm_port=5001
    )
    
    # 获取初始截图
    screenshot = agent.take_screenshot()
    obs = {"screenshot": screenshot}
    
    # 测试预测
    instruction = "Open Google Chrome"
    thought, actions, code = agent.predict(instruction, obs)
    
    print(f"Thought: {thought[:100]}...")
    print(f"Actions: {actions}")
    print(f"Code: {code}")
