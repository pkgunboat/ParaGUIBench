import ast
import base64
import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Dict, List

import backoff
import numpy as np
from PIL import Image
from requests.exceptions import SSLError
import openai
from openai import OpenAI

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.api_config import get_api_config

try:
    from google.api_core.exceptions import (
        BadRequest,
        InternalServerError,
        InvalidArgument,
        ResourceExhausted,
    )
except ImportError:
    # Google API exceptions are optional
    BadRequest = Exception
    InternalServerError = Exception
    InvalidArgument = Exception
    ResourceExhausted = Exception

import sys
import os
# Add parent directory to path to import utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import from utils (which re-exports from ui_tars)
# 注意: 这些导入在适配器中仍然使用,但不在主类中直接使用
from utils.prompt import COMPUTER_USE_DOUBAO, COMPUTER_USE_GUI_AGENT
from utils.action_parser import parse_action_to_structure_output, parsing_response_to_pyautogui_code, linear_resize, smart_resize, add_box_token
from utils.gui_agent_tools import pil_to_base64
FINISH_WORD = "finished"
WAIT_WORD = "wait"

class GUIAgent:
    def __init__(
        self,
        platform="ubuntu",
        action_space="pyautogui",
        observation_type="screenshot",
        # observation_type can be in ["screenshot", "a11y_tree", "screenshot_a11y_tree", "som"]
        max_trajectory_length=50,
        a11y_tree_max_tokens=10000,
        model_type="qwen",
        runtime_conf: dict = {
            # "infer_mode": "doubao",
            # "prompt_style": "doubao",
            "input_swap": False,
            "language": "English",
            "history_n": 5,
            "qwen_display_width": 1000,
            "qwen_display_height": 1000,
            "max_pixels": 16384*28*28,
            "min_pixels": 100*28*28,
            "callusr_tolerance": 3,
            "temperature": 0.0,
            "top_k": -1,
            "top_p": 0.9,
            "max_tokens": 500
        }
    ):
        self.platform = platform
        self.action_space = action_space
        self.observation_type = observation_type
        self.max_trajectory_length = max_trajectory_length
        self.a11y_tree_max_tokens = a11y_tree_max_tokens
        self.model_type = model_type
        self.runtime_conf = runtime_conf
        
        # 根据 model_type 配置 API 客户端
        if self.model_type == "qwen":
            # Qwen 模型配置
            self.vlm = OpenAI(
                api_key=runtime_conf.get("qwen_api_key", "${OPENAI_API_KEY}"),
                base_url=runtime_conf.get("qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.model_name = runtime_conf.get("qwen_model_name", "qwen3-vl-8b-thinking")
        elif self.model_type == "gpt":
            # GPT 模型配置（GPT-5）
            _api_config = get_api_config("deerapi")
            self.vlm = OpenAI(
                api_key=runtime_conf.get("gpt_api_key", runtime_conf.get("openai_api_key", _api_config["api_key"])),
                base_url=runtime_conf.get("gpt_base_url", runtime_conf.get("openai_base_url", _api_config["base_url"])),
            )
            self.model_name = runtime_conf.get("gpt_model_name", runtime_conf.get("openai_model_name", "gpt-5-2025-08-07"))
        elif self.model_type == "claude":
            # Claude 模型配置 - 使用 OpenAI 兼容格式（通过 deerapi 等代理）
            _api_config = get_api_config("claude")
            self.vlm = OpenAI(
                api_key=runtime_conf.get("claude_api_key", runtime_conf.get("openai_api_key", _api_config["api_key"])),
                base_url=runtime_conf.get("claude_base_url", runtime_conf.get("openai_base_url", _api_config["base_url"])),
            )
            self.model_name = runtime_conf.get("claude_model_name", "claude-sonnet-4-5")
        elif self.model_type == "doubao":
            # Doubao 模型配置（使用 deerapi）
            _api_config = get_api_config("deerapi")
            self.vlm = OpenAI(
                api_key=runtime_conf.get("doubao_api_key", _api_config["api_key"]),
                base_url=runtime_conf.get("doubao_base_url", _api_config["base_url"]),
            )
            self.model_name = runtime_conf.get("doubao_model_name", "doubao-seed-1-8-251228")
        else:
            # Doubao/UI-TARS 模型配置(默认 - 保留旧版本兼容)
            self.vlm = OpenAI(
                api_key="sk-6YlehOHzB5xV5G-zNRtfFg",
                base_url="https://litellm.mybigai.ac.cn/",
            )
            self.model_name = "doubao-1-5-ui-tars-250428"
            # self.vlm = OpenAI(
            #     api_key="${OPENAI_API_KEY}", 
            #     base_url="https://api.deerapi.com/v1/",
            # )
        
        # 初始化模型适配器
        # 如果是 Qwen 模型，默认使用 Qwen VL adapter（1000x1000 坐标系统）
        use_qwen_vl = runtime_conf.get("use_qwen_vl", True) if self.model_type == "qwen" else False
        try:
            from model_adapters import create_adapter
            self.model_adapter = create_adapter(self.model_type, self.runtime_conf, use_qwen_vl=use_qwen_vl)
        except ImportError:
            # 如果导入失败,尝试相对导入
            from .model_adapters import create_adapter
            self.model_adapter = create_adapter(self.model_type, self.runtime_conf, use_qwen_vl=use_qwen_vl)
        
        # 使用 .get() 方法安全访问配置,提供默认值
        self.temperature = self.runtime_conf.get("temperature", 0.0)
        self.top_k = self.runtime_conf.get("top_k", -1)
        self.top_p = self.runtime_conf.get("top_p", 0.9)
        self.max_tokens = self.runtime_conf.get("max_tokens", 500)
        # self.infer_mode = self.runtime_conf.get("infer_mode", "doubao")
        # self.prompt_style = self.runtime_conf.get("prompt_style", "doubao")
        self.input_swap = self.runtime_conf.get("input_swap", False)
        self.language = self.runtime_conf.get("language", "English")
        self.max_pixels = self.runtime_conf.get("max_pixels", 16384*28*28)
        self.min_pixels = self.runtime_conf.get("min_pixels", 100*28*28)
        self.callusr_tolerance = self.runtime_conf.get("callusr_tolerance", 3)

        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_images = []
        self.history_responses = []
        
        self.action_parse_res_factor = 1000
        self.history_n = self.runtime_conf.get("history_n", 5)
        
        self.cur_callusr_count = 0
        self.last_inference_time = None

    def predict(
        self, instruction: str, obs: Dict, last_action_after_obs: Dict = None
    ) -> List:
        """
        Predict the next action(s) based on the current observation.
        """

        # Append trajectory
        # print(len(self.observations), len(self.actions), len(self.actions))
        assert len(self.observations) == len(self.actions) and len(self.actions) == len(
            self.thoughts
        ), "The number of observations and actions should be the same."

        if len(self.observations) > self.max_trajectory_length:
            if self.max_trajectory_length == 0:
                _observations = []
                _actions = []
                _thoughts = []
            else:
                _observations = self.observations[-self.max_trajectory_length :]
                _actions = self.actions[-self.max_trajectory_length :]
                _thoughts = self.thoughts[-self.max_trajectory_length :]
        else:
            _observations = self.observations
            _actions = self.actions
            _thoughts = self.thoughts

        for previous_obs, previous_action, previous_thought in zip(
            _observations, _actions, _thoughts
        ):
            # {{{1
            if self.observation_type=='screenshot':
                _screenshot = previous_obs['screenshot']
                
            else:
                raise ValueError(
                    "Invalid observation_type type: " + self.observation_type
                )  # 1}}}

        self.history_images.append(obs["screenshot"])

        if self.observation_type in ["screenshot"]:
            base64_image = obs["screenshot"]
            self.observations.append(
                {"screenshot": base64_image, "accessibility_tree": None}
            )

        else:
            raise ValueError(
                "Invalid observation_type type: " + self.observation_type
            )  # 1}}}
        # 限制历史图片数量
        if len(self.history_images) > self.history_n:
            self.history_images = self.history_images[-self.history_n:]
        
        # 使用适配器构建消息
        try:
            adapter_output = self.model_adapter.build_messages(
                instruction=instruction,
                history_images=self.history_images,
                history_responses=self.history_responses,
                current_screenshot=obs["screenshot"]
            )
            
            # 适配器可能返回 (messages, image_info) 或 messages
            if isinstance(adapter_output, tuple):
                messages_data, image_info = adapter_output
            else:
                messages_data = adapter_output
                image_info = None
            
            # 如果是 Doubao 适配器, messages_data 可能是 (messages, last_image)
            if isinstance(messages_data, tuple):
                messages = messages_data[0]
                last_image = messages_data[1]
            else:
                messages = messages_data
                last_image = None
            
            # 获取图像尺寸
            if last_image:
                obs_image_height = last_image.height
                obs_image_width = last_image.width
            else:
                last_image = Image.open(BytesIO(self.history_images[-1]))
                obs_image_height = last_image.height
                obs_image_width = last_image.width
            
        except Exception as e:
            print(f"Error building messages: {e}")
            import traceback
            traceback.print_exc()
            return "client error", ["DONE"], "DONE"
        
        # ========== 阶段1: Round开始到API调用（准备阶段）==========
        round_start_time = time.time()
        preparation_time = round_start_time - time.time()  # 准备阶段很短
        print(f"[TIMING] Preparation: {preparation_time:.3f}s")
        
        # 调用模型(保持重试逻辑)
        try_times = 3
        temperature = self.temperature
        top_k = self.top_k
        prediction = None
        parsed_responses = None
        
        # ========== 阶段2: API调用时间 ==========
        while try_times > 0:
            try:
                call_start_time = time.time()
                prediction = self.model_adapter.call_model(
                    messages=(messages, image_info) if image_info else messages,
                    vlm_client=self.vlm,
                    model_name=self.model_name,
                    temperature=temperature,
                    max_tokens=self.max_tokens
                )
                call_end_time = time.time()
                last_call_duration = call_end_time - call_start_time
                print(f"[TIMING] API Call: {last_call_duration:.3f}s")
                
                print("starting prediction")
                if prediction:
                    # 处理不同类型的 prediction（可能是字符串或 API response 对象）
                    if isinstance(prediction, str):
                        pred_str = prediction
                    else:
                        # API response 对象，尝试提取文本内容用于日志
                        try:
                            pred_str = str(prediction.choices[0].message.content or prediction.choices[0].message.tool_calls)
                        except:
                            pred_str = str(type(prediction))
                    print(f'prediction: {pred_str[:200]}...' if len(pred_str) > 200 else f'prediction: {pred_str}')
                
                # 解析响应
                parsed_responses = self.model_adapter.parse_response(
                    prediction,
                    image_width=obs_image_width,
                    image_height=obs_image_height,
                    image_info=image_info,
                    last_image=last_image if not image_info else None
                )
                
                if parsed_responses:
                    print("parsed_responses: \n*************\n{}\n*************".format(parsed_responses))
                    self.last_inference_time = last_call_duration
                    break
                else:
                    print("Failed to parse response, retrying...")
                    try_times -= 1
                    
            except Exception as e:
                print(f"Error when fetching/parsing response: {e}")
                import traceback
                traceback.print_exc()
                prediction = None
                try_times -= 1
                temperature = 1
                top_k = -1
        
        if try_times <= 0 or prediction is None or parsed_responses is None:
            print(f"Reach max retry times to fetch response from client, as error flag.")
            return "client error", ["DONE"], "DONE"
        
        # 提取 reasoning（如果是 Qwen 模型）
        reasoning_text = ""
        if self.model_type == "qwen" and parsed_responses:
            for parsed_response in parsed_responses:
                if isinstance(parsed_response, dict) and "reasoning" in parsed_response:
                    reasoning_text = parsed_response.get("reasoning", "")
                    print(f"[DEBUG gui_agent] Extracted reasoning: '{reasoning_text[:100]}...'")
                    break
        
        # 提取 prediction 的文本内容（用于返回）
        if isinstance(prediction, str):
            prediction_text = prediction
        else:
            # API response 对象，提取文本内容
            try:
                prediction_text = prediction.choices[0].message.content or ""
            except:
                prediction_text = str(prediction)
        
        print(f"[DEBUG gui_agent] reasoning_text: '{reasoning_text[:50] if reasoning_text else 'EMPTY'}', prediction_text: '{prediction_text[:50] if prediction_text else 'EMPTY'}'")
        
        # 如果有 reasoning，优先使用 reasoning 作为 thought
        if reasoning_text:
            final_thought = reasoning_text
        else:
            final_thought = prediction_text
        
        # 保存到历史（保存原始对象以便后续处理）
        self.history_responses.append(prediction)
        self.thoughts.append(final_thought)  # 使用 reasoning 或原始文本
        
        # ========== 阶段3: Response解析到执行完毕 ==========
        parsing_start_time = time.time()
        
        # 转换为 pyautogui 代码
        actions = []
        pyautogui_code = ""
        
        for parsed_response in parsed_responses:
            # 检查是否是特殊动作(针对 Doubao 适配器)
            if isinstance(parsed_response, dict) and "action_type" in parsed_response:
                action_type = parsed_response["action_type"]
                if action_type == FINISH_WORD:
                    self.actions.append(actions)
                    # 提取 finished(content='...') 中的实际内容
                    content = ""
                    if "action_inputs" in parsed_response and isinstance(parsed_response["action_inputs"], dict):
                        content = parsed_response["action_inputs"].get("content", "")
                    # 将内容作为特殊代码返回，格式：DONE:<content>
                    # 这样 tool wrapper 可以提取实际数据
                    return final_thought, ["DONE"], f"DONE:{content}" if content else "DONE"
                elif action_type == WAIT_WORD:
                    self.actions.append(actions)
                    return final_thought, ["WAIT"], "WAIT"
            
            # 转换为 pyautogui 代码
            parsed_pyautogui_code = self.model_adapter.response_to_code(
                parsed_responses=[parsed_response],
                image_width=obs_image_width,
                image_height=obs_image_height,
                image_info=image_info,
                last_image=last_image if not image_info else None
            )
            
            # 检查特殊返回值
            if parsed_pyautogui_code in ["DONE", "WAIT"]:
                self.actions.append(actions)
                return final_thought, [parsed_pyautogui_code], parsed_pyautogui_code
            
            if parsed_pyautogui_code:
                actions.append(parsed_pyautogui_code)
                pyautogui_code = parsed_pyautogui_code
        
        self.actions.append(actions)
        
        # 检查是否超过最大步数
        if len(self.history_responses) >= self.max_trajectory_length:
            actions = ["FAIL"]
            pyautogui_code = "FAIL"
        
        parsing_end_time = time.time()
        parsing_time = parsing_end_time - parsing_start_time
        print(f"[TIMING] Parsing & Execution: {parsing_time:.3f}s")
        
        # 总时间
        total_time = parsing_end_time - round_start_time
        print(f"[TIMING] Total Round: {total_time:.3f}s")
        print(f"[TIMING] Breakdown: Prep={preparation_time:.3f}s + API={last_call_duration:.3f}s + Parse&Exec={parsing_time:.3f}s")
        
        print("results:", final_thought[:100] if final_thought else None, actions, pyautogui_code[:100] if pyautogui_code else None)
        return final_thought, actions, pyautogui_code


    @backoff.on_exception(
        backoff.constant,
        # here you should add more model exceptions as you want,
        # but you are forbidden to add "Exception", that is, a common type of exception
        # because we want to catch this kind of Exception in the outside to ensure each example won't exceed the time limit
        (
            # General exceptions
            SSLError,
            # OpenAI exceptions
            openai.RateLimitError,
            openai.BadRequestError,
            openai.InternalServerError,
            # Google exceptions
            InvalidArgument,
            ResourceExhausted,
            InternalServerError,
            BadRequest,
            # Groq exceptions
            # todo: check
        ),
        interval=30,
        max_tries=10,
    )
    
    def reset(self, runtime_logger):
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_images = []
        self.history_responses = []
    
    def check_screen_changed(self, before_screenshot, after_screenshot, threshold=0.02):
        """
        检查屏幕是否有显著变化
        
        Args:
            before_screenshot: 动作前的截图 (bytes)
            after_screenshot: 动作后的截图 (bytes)
            threshold: 变化阈值 (0-1),默认2%像素变化即认为有效
        
        Returns:
            (changed: bool, diff_ratio: float)
        """
        try:
            img_before = Image.open(BytesIO(before_screenshot))
            img_after = Image.open(BytesIO(after_screenshot))
            
            # 转换为numpy数组
            arr_before = np.array(img_before)
            arr_after = np.array(img_after)
            
            # 计算像素差异
            diff = np.abs(arr_before.astype(float) - arr_after.astype(float))
            # 如果任意RGB通道差异>10,认为像素变化了
            changed_pixels = np.any(diff > 10, axis=2)
            diff_ratio = np.sum(changed_pixels) / changed_pixels.size
            
            print(f"[SCREEN_CHANGE] Diff ratio: {diff_ratio:.4f} ({diff_ratio*100:.2f}%)")
            
            return diff_ratio > threshold, diff_ratio
        except Exception as e:
            print(f"[SCREEN_CHANGE] Error: {e}")
            # 发生错误时假设屏幕有变化,避免无限重试
            return True, 0.0
    
    def get_retry_offsets(self):
        """
        返回重试时的坐标偏移列表
        基于观察: Qwen-VL-Max 倾向于点击位置偏右上
        """
        return [
            (-50, 10),   # 向左偏移50像素,向下10像素 (补偿常见偏差)
            (-100, 20),  # 更大偏移
            (-80, 40),   # 左下方向
            (0, 30),     # 只向下
        ]

