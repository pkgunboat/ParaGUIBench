"""
code agent是一个通过后台使用命令行来完成任务的Agent,使用纯语言的方式交互
Agent输入: 包括用户指令、命令行的上下文
输出：执行的指令
"""
import re
from typing import Dict, List, Tuple
from openai import OpenAI

# 特殊关键词
FINISH_WORD = "DONE"
WAIT_WORD = "WAIT"
FAIL_WORD = "FAIL"

# System Prompt
CODE_AGENT_SYSTEM_PROMPT = """You are a highly intelligent AI assistant (GPT-5) that can execute Python code on an Ubuntu computer.

You will receive:
1. A task instruction
2. Terminal output from previous actions (if any)
3. Current working directory

🧠 IMPORTANT: Choose the right approach based on the task:

**Type A - Mechanical tasks** (file operations, data extraction, calculations):
→ Write code to complete the task directly

**Type B - Tasks requiring intelligence** (understanding, analysis, judgment, reasoning):
→ Use code only to GATHER information (read files, print content)
→ Use YOUR intelligence to ANALYZE and REASON
→ Write your conclusions as string literals, not algorithm outputs

⚠️ For Type B tasks, do NOT replace your thinking with code!
- BAD: Writing regex/keyword matching to "analyze" content
- BAD: Using algorithms to make judgments you should make yourself  
- GOOD: Reading content, understanding it, then writing your analysis directly

📝 Workflow for tasks requiring your intelligence:
1. Write code to read and print the content you need to understand
2. After seeing the terminal output, think about what it means
3. Write your analysis/conclusion directly as a string (your own words, not computed)
4. Save the result if needed

Output Format:
Thought: [Your reasoning in {language}]
Action:
```python
# Your Python code here
```

OR when task is complete:
Thought: [explanation]
Action:
DONE

OR when waiting is needed:
Thought: [explanation]
Action:
WAIT

Remember: For analytical tasks, use code to READ data, then use YOUR intelligence to UNDERSTAND and ANALYZE it.
"""


class CodeAgent:
    def __init__(
        self,
        platform="ubuntu",
        action_space="code",
        observation_type="text",
        max_trajectory_length=20,
        model_name="claude-sonnet-4-5",
        runtime_conf: dict = None
    ):
        """
        初始化 CodeAgent
        
        Args:
            platform: 平台类型，默认 "ubuntu"
            action_space: 动作空间，默认 "code"
            observation_type: 观察类型，默认 "text"
            max_trajectory_length: 最大轨迹长度
            model_name: 使用的模型名称
            runtime_conf: 运行时配置字典
        """
        # 基础配置
        self.platform = platform
        self.action_space = action_space
        self.observation_type = observation_type
        self.max_trajectory_length = max_trajectory_length
        self.model_name = model_name
        
        # 运行时配置
        if runtime_conf is None:
            runtime_conf = {
                "language": "English",
                "history_n": 15,
                "temperature": 0.0,
                "top_p": 0.9,
                "max_tokens": 16384,  # 增加到16K以容纳推理token + 输出内容
            }
        self.runtime_conf = runtime_conf
        
        # 从配置中提取参数
        self.language = self.runtime_conf.get("language", "English")
        self.history_n = self.runtime_conf.get("history_n", 15)
        self.temperature = self.runtime_conf.get("temperature", 0.0)
        self.top_p = self.runtime_conf.get("top_p", 0.9)
        self.max_tokens = self.runtime_conf.get("max_tokens", 4096)
        
        # 初始化 OpenAI 客户端
        self.vlm = OpenAI(
            api_key="${OPENAI_API_KEY}", 
            base_url="https://api.deerapi.com/v1/",
        )
        
        # 状态变量
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_responses = []

    def predict(
        self, 
        instruction: str, 
        obs: Dict
    ) -> Tuple[str, List[str], str]:
        """
        根据指令和观察预测下一步动作
        
        Args:
            instruction: 用户任务指令
            obs: 观察字典，包含：
                - terminal_output: 终端输出（字符串）
                - current_dir: 当前目录（可选）
        
        Returns:
            prediction: 模型的完整响应
            actions: 动作列表
            code: 要执行的Python代码字符串
        """
        # 1. 历史管理：截断到最大轨迹长度
        if len(self.observations) > self.max_trajectory_length:
            _observations = self.observations[-self.max_trajectory_length:]
            _actions = self.actions[-self.max_trajectory_length:]
            _thoughts = self.thoughts[-self.max_trajectory_length:]
            _history_responses = self.history_responses[-self.max_trajectory_length:]
        else:
            _observations = self.observations
            _actions = self.actions
            _thoughts = self.thoughts
            _history_responses = self.history_responses
        
        # 2. 添加当前观察到历史
        terminal_output = obs.get("terminal_output", "")
        current_dir = obs.get("current_dir", "/home/user")
        
        current_obs_text = f"Terminal Output:\n{terminal_output}\n\nCurrent Directory: {current_dir}"
        self.observations.append(current_obs_text)
        
        # 3. 构建消息列表
        messages = []
        
        # 3.1 添加 System Prompt
        system_prompt = CODE_AGENT_SYSTEM_PROMPT.format(language=self.language)
        messages.append({
            "role": "system",
            "content": system_prompt
        })
        
        # 3.2 添加历史对话（只保留最近 history_n 轮）
        history_start_idx = max(0, len(_observations) - self.history_n - 1)  # -1 因为最后一个是当前观察
        
        for i in range(history_start_idx, len(_observations) - 1):  # 不包括刚添加的当前观察
            # User: 观察
            messages.append({
                "role": "user",
                "content": _observations[i]
            })
            # Assistant: 完整的模型响应（包含Thought和Action）
            if i < len(_history_responses):
                messages.append({
                    "role": "assistant",
                    "content": _history_responses[i]  # 使用完整响应，不是只有代码的actions
                })
        
        # 3.3 添加当前任务指令和观察
        current_user_message = f"Task: {instruction}\n\n{current_obs_text}"
        messages.append({
            "role": "user",
            "content": current_user_message
        })
        
        # 4. 调用 LLM
        try:
            # 构建请求参数（某些模型不支持 top_p）
            request_params = {
                "model": self.model_name,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            
            # 只有当 top_p 不是默认值时才添加（避免不支持的参数）
            if self.top_p != 0.9:
                request_params["top_p"] = self.top_p
            
            response = self.vlm.chat.completions.create(**request_params)
            
            # DEBUG: 打印API响应的详细信息
            print(f"[DEBUG] API response object:")
            print(f"  - id: {response.id}")
            print(f"  - model: {response.model}")
            print(f"  - choices count: {len(response.choices)}")
            if response.choices:
                print(f"  - finish_reason: {response.choices[0].finish_reason}")
                print(f"  - message.role: {response.choices[0].message.role}")
                content = response.choices[0].message.content
                print(f"  - message.content type: {type(content)}")
                print(f"  - message.content is None: {content is None}")
                if content:
                    print(f"  - message.content length: {len(content)}")
            
            prediction = response.choices[0].message.content
            if prediction:
                prediction = prediction.strip()
            else:
                prediction = ""
            
            print(f"Model Response:\n{prediction}\n")
            
            # DEBUG: 检测空响应
            if not prediction or len(prediction) < 10:
                print(f"[DEBUG] ⚠️ Empty or short response detected!")
                print(f"[DEBUG] Response length: {len(prediction)}")
                print(f"[DEBUG] Response content: {repr(prediction)}")
                print(f"[DEBUG] Full response object: {response}")
                print(f"[DEBUG] Messages sent to API:")
                for idx, msg in enumerate(messages):
                    print(f"  Message {idx}: role={msg['role']}, content_len={len(msg.get('content', ''))}")
                    if len(msg.get('content', '')) < 500:
                        print(f"    Content: {msg.get('content', '')[:200]}")
                
        except Exception as e:
            print(f"Error calling LLM: {e}")
            import traceback
            traceback.print_exc()
            return "Error", ["FAIL"], "FAIL"
        
        # 5. 解析响应
        # 5.1 先提取代码块
        code = self._extract_code_from_response(prediction)
        print(f"[DEBUG] Extracted code: {repr(code[:100] if code else None)}")
        
        # 5.2 检查特殊关键词
        # 如果没有提取到代码,或者提取到的"代码"就是特殊关键词
        if code is None or code.strip() == "":
            print(f"[DEBUG] No code extracted, checking for keywords in response...")
            print(f"[DEBUG] FINISH_WORD in prediction: {FINISH_WORD in prediction}")
            print(f"[DEBUG] WAIT_WORD in prediction: {WAIT_WORD in prediction}")
            
            if FINISH_WORD in prediction:
                self.thoughts.append(prediction)
                self.actions.append("DONE")
                self.history_responses.append(prediction)
                return prediction, ["DONE"], "DONE"
            
            if WAIT_WORD in prediction:
                self.thoughts.append(prediction)
                self.actions.append("WAIT")
                self.history_responses.append(prediction)
                return prediction, ["WAIT"], "WAIT"
            
            # 既没有代码也没有特殊关键词
            print("Warning: No code block found in response and no special keyword")
            print(f"[DEBUG] Response preview (first 500 chars):\n{prediction[:500]}")
            code = "FAIL"
        
        # 5.3 检查提取的代码是否就是特殊关键词
        elif code.strip() in [FINISH_WORD, WAIT_WORD, FAIL_WORD]:
            self.thoughts.append(prediction)
            self.actions.append(code.strip())
            self.history_responses.append(prediction)
            return prediction, [code.strip()], code.strip()
        
        # 6. 更新状态
        self.thoughts.append(prediction)
        self.actions.append(code)
        self.history_responses.append(prediction)
        
        # 7. 返回结果
        actions_list = [code] if code not in ["DONE", "WAIT", "FAIL"] else [code]
        
        return prediction, actions_list, code

    def _extract_code_from_response(self, response: str) -> str:
        """
        从响应中提取代码块
        
        支持格式:
        - ```python ... ```
        - ```bash ... ```
        - ``` ... ```
        
        Args:
            response: 模型的响应文本
        
        Returns:
            提取的代码字符串，如果没找到返回 None
        """
        # 尝试匹配 ```python ... ```
        pattern_python = r'```python\s*\n(.*?)```'
        match = re.search(pattern_python, response, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 尝试匹配 ```bash ... ```
        pattern_bash = r'```bash\s*\n(.*?)```'
        match = re.search(pattern_bash, response, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 尝试匹配 ``` ... ```
        pattern_generic = r'```\s*\n(.*?)```'
        match = re.search(pattern_generic, response, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 如果没有代码块，尝试提取 Action: 后面的内容
        pattern_action = r'Action:\s*\n(.*?)(?:\n\n|$)'
        match = re.search(pattern_action, response, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        return None

    def reset(self, runtime_logger=None):
        """
        重置 Agent 状态，准备执行新任务
        
        Args:
            runtime_logger: 日志记录器（可选，保持接口兼容）
        """
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_responses = []
        print("CodeAgent reset: All state cleared")
