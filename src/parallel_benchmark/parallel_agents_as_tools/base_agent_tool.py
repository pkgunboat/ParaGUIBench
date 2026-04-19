"""
Base class for Agent Tools
所有 Agent Tool 的基类,提供统一接口
"""
import base64
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseAgentTool(ABC):
    """Agent Tool 基类"""

    def __init__(self, controller):
        """
        初始化 Agent Tool

        Args:
            controller: PythonController 实例,用于与虚拟机交互
        """
        self.controller = controller
    
    @abstractmethod
    def execute(self, task: str, max_rounds: int = 10, timeout: int = 300) -> Dict:
        """
        执行任务(子类必须实现)
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次
            timeout: 超时时间(秒)
        
        Returns:
            结果字典,格式:
            {
                "status": "success" | "failure",
                "result": "任务执行结果描述",
                "steps": ["step1", "step2", ...],
                "error": "错误信息(如果有)"
            }
        """
        pass
    
    def format_result(
        self, 
        success: bool, 
        result: str, 
        steps: List[str],
        error: str = None,
        rounds_timing: List[Dict] = None,
        gui_token_usage: Dict = None
    ) -> Dict:
        """
        统一的结果格式化
        
        Args:
            success: 是否成功
            result: 结果描述
            steps: 执行步骤列表
            error: 错误信息(可选)
            rounds_timing: 每轮的详细时间信息(可选)
            gui_token_usage: GUI Agent 的 token 消耗统计(可选)
        
        Returns:
            格式化的结果字典
        """
        result_dict = {
            "status": "success" if success else "failure",
            "result": result,
            "steps": steps,
            "error": error if not success else None
        }
        
        # 添加详细的轮次时间信息
        if rounds_timing:
            result_dict["rounds_timing"] = rounds_timing
        
        # 添加 GUI Agent token usage（供上层 Plan Agent 汇总）
        if gui_token_usage:
            result_dict["gui_token_usage"] = gui_token_usage

        return result_dict

    # ------------------------------------------------------------------
    # 反思总结：所有 GUI Agent Tool 通用
    # ------------------------------------------------------------------

    def _get_last_screenshot_b64(self) -> str:
        """
        从 controller 获取当前屏幕截图并转为 base64 编码。

        输出:
            str: 截图的 base64 字符串，失败返回空字符串
        """
        try:
            screenshot_bytes = self.controller.get_screenshot()
            if screenshot_bytes:
                return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception as e:
            print(f"[WARN] Failed to get screenshot for reflection: {e}")
        return ""

    def _generate_reflection_summary(
        self,
        task: str,
        steps: list,
        thoughts: list,
        final_status: str,
        last_screenshot_b64: str = "",
        client=None,
        model_name: str = "",
    ) -> str:
        """
        调用模型生成执行反思总结，附带最后一张截图和完整执行轨迹，返回给 Plan Agent。

        所有 GUI Agent Tool 共用此方法，通过传入各自的 client 和 model_name 来复用。
        client 需兼容 OpenAI SDK 接口（client.chat.completions.create）。

        输入:
            task: 原始任务描述
            steps: 执行步骤记录列表（List[Dict] 或 List[str]）
            thoughts: 思考过程列表（每轮完整文本）
            final_status: 最终状态 ("success" / "failure" / "max_rounds")
            last_screenshot_b64: 最后一张截图的 base64 编码（可选，为空时自动获取）
            client: OpenAI 兼容的模型客户端（必需）
            model_name: 模型名称（必需）
        输出:
            str: 结构化的反思总结
        """
        if client is None or not model_name:
            # 没有可用的 client，回退到 thoughts 内容
            return thoughts[-1][:500] if thoughts else "No summary available"

        # 如果没有传入截图，尝试从 controller 获取
        if not last_screenshot_b64:
            last_screenshot_b64 = self._get_last_screenshot_b64()

        # 组装完整执行轨迹：每轮的完整 thought + action，不做截断
        trajectory_lines = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                round_num = step.get("round", i + 1)
                action_str = step.get("action", "") or step.get("code", "")
            else:
                round_num = i + 1
                action_str = str(step)
            thought = thoughts[i] if i < len(thoughts) else ""
            trajectory_lines.append(
                f"Round {round_num}:\n  [Thought] {thought}\n  [Action] {action_str}"
            )
        trajectory_text = "\n".join(trajectory_lines)

        summary_prompt = (
            f"You just completed a GUI automation task. The attached image shows the current screen state.\n\n"
            f"Task: {task}\n"
            f"Status: {final_status}\n"
            f"Rounds executed: {len(steps)}\n\n"
            f"Full execution trajectory:\n{trajectory_text}\n\n"
            f"Based on the screenshot and execution history, provide a concise summary (2-4 sentences):\n"
            f"1. What did you accomplish?\n"
            f"2. What problems did you encounter (if any)?\n"
            f"3. What is the current state visible on the screen?"
        )

        try:
            # 已知不支持 vision 的模型关键词（按需扩展）
            NON_VISION_KEYWORDS = ["gpt-4o-mini", "qwen", "deepseek"]
            is_vision = not any(kw in model_name.lower() for kw in NON_VISION_KEYWORDS)

            # 构建消息：如果有截图且模型支持 vision 则作为 vision 消息发送
            if last_screenshot_b64 and is_vision:
                user_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_screenshot_b64}"}},
                    {"type": "text", "text": summary_prompt},
                ]
            else:
                user_content = summary_prompt
            messages = [{"role": "user", "content": user_content}]

            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=512,
            )
            summary = completion.choices[0].message.content.strip()
            print(f"[{self.__class__.__name__}] Reflection summary generated: {summary[:200]}...")
            return summary
        except Exception as e:
            print(f"[WARN] Reflection summary failed: {e}")
            return thoughts[-1][:500] if thoughts else "No summary available"
