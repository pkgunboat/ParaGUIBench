"""
执行记录器 V2 (ExecutionRecorder)

将实时的执行日志转换为 record_template.json 格式 (v2.0)
支持实时记录和最终导出

本版本相比 v1 的主要变更：
1. coordinator -> plan_agent（输出格式变更，输入参数保持兼容）
2. 使用 TimeSpan 统一时间结构
3. 支持 dependencies 依赖关系记录
4. 输出格式完全符合 record_template.py 定义

使用方法:
    recorder = ExecutionRecorder(task_id="task_xxx", instruction="查询天气")
    
    # 开始记录
    recorder.start()
    
    # 记录 Plan Agent 轮次
    recorder.add_plan_agent_round(...)
    
    # 记录 Code Agent 轮次  
    recorder.add_code_agent_round(...)
    
    # 结束并保存
    recorder.finish()
    recorder.save("output.json")
"""

import json
import time
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict


# ============== 数据类定义 ==============

@dataclass
class TimeSpan:
    """
    时间跨度类，记录操作的起止时间和持续时长。
    
    Attributes:
        start_timestamp: 开始时间戳（Unix 时间戳）
        start_time_iso: 开始时间的 ISO 格式字符串
        end_timestamp: 结束时间戳（Unix 时间戳）
        end_time_iso: 结束时间的 ISO 格式字符串
        duration: 持续时长（秒）
    """
    start_timestamp: float = 0.0
    start_time_iso: str = ""
    end_timestamp: float = 0.0
    end_time_iso: str = ""
    duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "start_timestamp": self.start_timestamp,
            "start_time_iso": self.start_time_iso,
            "end_timestamp": self.end_timestamp,
            "end_time_iso": self.end_time_iso,
            "duration": self.duration
        }
    
    @classmethod
    def create_now(cls) -> 'TimeSpan':
        """创建一个以当前时间为起点的 TimeSpan。"""
        now = datetime.now(timezone.utc)
        return cls(
            start_timestamp=now.timestamp(),
            start_time_iso=now.isoformat()
        )
    
    def end_now(self) -> 'TimeSpan':
        """设置结束时间为当前时间，并计算持续时长。"""
        now = datetime.now(timezone.utc)
        self.end_timestamp = now.timestamp()
        self.end_time_iso = now.isoformat()
        self.duration = self.end_timestamp - self.start_timestamp
        return self


@dataclass
class DependencyRef:
    """
    依赖引用类，表示对某个 Agent 某个轮次的依赖。
    
    Attributes:
        agent_id: 依赖的 Agent 调用 ID，如 "gui_agent_1"
        round: 依赖的轮次号
        reason: 依赖原因说明
    """
    agent_id: str = ""
    round: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "agent_id": self.agent_id,
            "round": self.round,
            "reason": self.reason
        }


@dataclass
class AgentDependency:
    """
    Agent 依赖信息类，记录某个被调度 Agent 的依赖关系。
    
    Attributes:
        round: 该 Agent 被调度的轮次
        task: 该 Agent 的任务描述
        depends_on: 依赖的其他 Agent 列表
    """
    round: int = 0
    task: str = ""
    depends_on: List[DependencyRef] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "round": self.round,
            "task": self.task,
            "depends_on": [d.to_dict() if isinstance(d, DependencyRef) else d for d in self.depends_on]
        }


# ============== 主类 ==============

class ExecutionRecorder:
    """
    执行记录器 V2 - 将执行日志转换为 record_template.json 格式
    
    输出格式符合 record_template.py 定义的 v2.0.0 规范。
    """
    
    VERSION = "2.0.0"
    
    def __init__(
        self, 
        task_id: str = "",
        instruction: str = "",
        coordinator_model: str = "gpt-5",  # 保持参数名兼容
        device_id: str = "Desktop-1",
        device_type: str = "desktop",
        coordinator_system_prompt: str = ""  # 保持参数名兼容
    ):
        """
        初始化记录器
        
        Args:
            task_id: 任务ID
            instruction: 用户指令
            coordinator_model: Plan Agent 使用的模型名称（参数名保持兼容）
            device_id: 默认设备ID
            device_type: 设备类型
            coordinator_system_prompt: Plan Agent 的 system prompt（参数名保持兼容）
        """
        self.task_id = task_id
        self.instruction = instruction
        
        # 内部使用 plan_agent 命名，但构造函数参数保持兼容
        self.plan_agent_model = coordinator_model
        self.plan_agent_system_prompt = coordinator_system_prompt
        
        self.device_id = device_id
        self.device_type = device_type
        
        # 时间记录
        self.start_timestamp: float = 0.0
        self.start_time_iso: str = ""
        self.end_timestamp: float = 0.0
        self.end_time_iso: str = ""
        
        # Plan Agent 数据
        self.plan_agent_rounds: List[Dict] = []
        self.plan_agent_full_messages: List[Dict] = []  # 存储完整的对话历史
        
        # Code/GUI Agents 数据 (agent_count -> agent_data)
        # agent_count 格式: "{agent_type}_call_{global_count}" 例如 "code_agent_call_1"
        self.agents: Dict[str, Dict] = {}
        
        # 追踪每个 agent 类型的全局调用次数 (agent_type -> global_count)
        # 用于生成 agent_count，例如 gui_agent: 3 表示全局第3次调用GUI
        self._agent_global_counts: Dict[str, int] = {}
        
        # 追踪每个设备上每个 agent 的本地调用次数 ((device_id, agent_id) -> local_count)
        # 用于生成 call_id，例如 (Desktop-0, gui_agent): 2 表示该设备第2次调用GUI
        self._agent_local_counts: Dict[tuple, int] = {}
        
        # 是否已开始
        self._started = False
        
        # 总体任务成功状态（可手动设置，默认None表示自动判断）
        self._overall_success: Optional[bool] = None
        
        # 最终答案（Plan Agent 对任务的最终回答）
        self.final_answer: str = ""
        
    def start(self):
        """开始记录"""
        self.start_timestamp = time.time()
        self.start_time_iso = datetime.now(timezone.utc).isoformat()
        self._started = True
        
    def finish(self):
        """结束记录"""
        self.end_timestamp = time.time()
        self.end_time_iso = datetime.now(timezone.utc).isoformat()
    
    # =====================================================================
    # 别名方法 - 为了兼容调用代码
    # =====================================================================
    
    def start_task(self):
        """start() 的别名"""
        return self.start()
    
    def finish_task(self, success: bool = True, final_answer: str = ""):
        """
        结束任务并设置总体成功状态
        
        Args:
            success: 总体任务是否成功（True=成功，False=失败）
            final_answer: 任务的最终答案（可选）
        """
        self._overall_success = success
        if final_answer:
            self.final_answer = final_answer
        return self.finish()
    
    def set_final_answer(self, answer: str):
        """
        设置任务的最终答案
        
        Args:
            answer: 最终答案文本
        """
        self.final_answer = answer
    
    def get_record(self) -> Dict[str, Any]:
        """to_dict() 的别名"""
        return self.to_dict()
    
    # =====================================================================
    # Plan Agent 相关方法
    # =====================================================================
    
    def add_plan_agent_round(
        self,
        round_num: int,
        model_start_time: float,
        model_end_time: float,
        response: str,
        actions: List[Dict] = None,
        thought: Dict = None,  # 兼容旧调用方式
        action_start_time: Optional[float] = None,
        action_end_time: Optional[float] = None,
        action_result: Optional[Dict] = None,
        dispatched_agents: Optional[List[str]] = None,
        messages: Optional[List[Dict]] = None,
        dependencies: Optional[Dict] = None
    ):
        """
        添加 Plan Agent 轮次记录
        
        Args:
            round_num: 轮次编号 (0-based)
            model_start_time: 模型调用开始时间戳
            model_end_time: 模型调用结束时间戳
            response: 模型原始响应
            actions: 解析出的动作列表
            thought: 思考内容（兼容参数，会转换为 actions）
            action_start_time: 动作执行开始时间戳
            action_end_time: 动作执行结束时间戳
            action_result: 动作执行结果
            dispatched_agents: 调度的 agent ID 列表
            messages: 对话消息列表（messages[0]应为system prompt）
            dependencies: 依赖关系字典，格式:
                {
                    "agent_count": {
                        "round": 1,
                        "task": "任务描述",
                        "depends_on": [
                            {"agent_id": "other_agent", "round": 0, "reason": "原因"}
                        ]
                    }
                }
        """
        # 如果传入的是 thought 而不是 actions，转换为 actions 格式
        if actions is None and thought is not None:
            actions = [{"type": "thought", "data": thought}]
        elif actions is None:
            actions = []
        
        # 更新完整的 messages（如果提供），过滤掉system消息
        if messages:
            # 过滤掉system消息，只保留user和assistant消息
            self.plan_agent_full_messages = [msg for msg in messages if msg.get('role') != 'system']
            # Plan Agent: 保存从最后一个 assistant 开始的所有消息
            # 包括 assistant (with tool_calls) + 所有 tool + user
            # 这样可以处理并行调用产生多个 tool 消息的情况
            filtered_messages = [msg for msg in messages if msg.get('role') != 'system']
            # 找到最后一个 assistant 消息的位置
            last_assistant_idx = -1
            for i in range(len(filtered_messages) - 1, -1, -1):
                if filtered_messages[i].get('role') == 'assistant':
                    last_assistant_idx = i
                    break
            # 从最后一个 assistant 开始保存所有后续消息
            if last_assistant_idx >= 0:
                new_messages = filtered_messages[last_assistant_idx:]
            else:
                new_messages = filtered_messages[-3:] if len(filtered_messages) >= 3 else filtered_messages
        else:
            new_messages = []
        
        model_duration = model_end_time - model_start_time
        
        # 构建 model_prediction，使用 TimeSpan 结构
        model_prediction = {
            "time_span": {
                "start_timestamp": model_start_time,
                "start_time_iso": datetime.fromtimestamp(model_start_time, tz=timezone.utc).isoformat(),
                "end_timestamp": model_end_time,
                "end_time_iso": datetime.fromtimestamp(model_end_time, tz=timezone.utc).isoformat(),
                "duration": model_duration
            },
            "response": response,
            "actions": actions,
            "messages": new_messages  # 本轮新增的消息
        }
        
        round_data = {
            "round_id": round_num,
            "model_prediction": model_prediction,
            "action_execution": None,
            "total_duration": model_duration
        }
        
        # 添加依赖关系（如果提供）
        if dependencies:
            round_data["dependencies"] = dependencies
        
        # 处理 action_execution
        if action_start_time and action_end_time:
            action_duration = action_end_time - action_start_time
            
            execution_result = action_result or {
                "status": "success",
                "returncode": 0,
                "output": "",
                "error": ""
            }
            
            if dispatched_agents:
                execution_result["dispatched_agents"] = dispatched_agents
            
            round_data["action_execution"] = {
                "time_span": {
                    "start_timestamp": action_start_time,
                    "start_time_iso": datetime.fromtimestamp(action_start_time, tz=timezone.utc).isoformat(),
                    "end_timestamp": action_end_time,
                    "end_time_iso": datetime.fromtimestamp(action_end_time, tz=timezone.utc).isoformat(),
                    "duration": action_duration
                },
                "result": execution_result
            }
            
            round_data["total_duration"] = model_duration + action_duration
        
        self.plan_agent_rounds.append(round_data)
    
    # =====================================================================
    # Code Agent 相关方法
    # =====================================================================
    
    def add_code_agent(
        self,
        agent_id: str,
        task: str,
        model_name: str = "gpt-5",
        parent_round: int = 0,
        code_type: str = "python",
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        device_id: Optional[str] = None,
        system_prompt: str = ""
    ) -> str:
        """
        添加 Code Agent 的一次新调用
        
        每次调用都会创建一个新的独立记录，使用 call_id 区分同一个 agent 的不同调用。
        
        Args:
            agent_id: Agent ID (如 "code_agent_1")
            task: 分配的任务
            model_name: 使用的模型
            parent_round: 父轮次 (Plan Agent 的哪一轮调度的)
            code_type: 代码类型
            start_timestamp: 开始时间戳（可选，如果不提供则使用当前时间）
            end_timestamp: 结束时间戳（可选）
            device_id: 设备 ID (如 "Desktop-1", "Desktop-2"，可选)
            system_prompt: Code Agent的system prompt
            
        Returns:
            agent_count: 本次调用的唯一标识符，格式为 "{agent_type}_call_{global_count}"
        """
        # 使用传入的时间戳，或者当前时间
        start_ts = start_timestamp if start_timestamp else time.time()
        end_ts = end_timestamp if end_timestamp else 0.0
        duration = (end_ts - start_ts) if end_ts > 0 else 0.0
        
        # 确定 agent_type (从agent_id提取)
        agent_type = "code_agent"
        
        # 全局计数 - 用于 agent_count
        if agent_type not in self._agent_global_counts:
            self._agent_global_counts[agent_type] = 0
        self._agent_global_counts[agent_type] += 1
        global_count = self._agent_global_counts[agent_type]
        
        # 本地计数 - 用于 call_id (针对特定的 device_id 和 agent_id)
        device = device_id or self.device_id
        local_key = (device, agent_id)
        if local_key not in self._agent_local_counts:
            self._agent_local_counts[local_key] = 0
        self._agent_local_counts[local_key] += 1
        call_id = self._agent_local_counts[local_key]
        
        # 生成唯一标识符 (使用全局计数)
        agent_count = f"{agent_type}_call_{global_count}"
        
        # 每次调用都创建新的独立记录
        self.agents[agent_count] = {
            "agent_id": agent_id,
            "agent_count": agent_count,
            "call_id": call_id,
            "type": "code",
            "task": task,
            "model_name": model_name,
            "code_type": code_type,
            "system_prompt": system_prompt,
            "parent_agent": "coordinator",
            "parent_round": parent_round,
            "device_id": device,
            "recording": {
                "start_timestamp": start_ts,
                "start_time_iso": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                "end_timestamp": end_ts,
                "end_time_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat() if end_ts > 0 else "",
                "duration": duration
            },
            "rounds": [],
            "summary": None,
            "full_messages": []
        }
        
        return agent_count
    
    def add_code_agent_round(
        self,
        agent_id: str,
        round_num: int,
        model_start_time: float,
        model_end_time: float,
        response: str,
        actions: List[Dict],
        code: Optional[str] = None,
        action_start_time: Optional[float] = None,
        action_end_time: Optional[float] = None,
        action_result: Optional[Dict] = None,
        is_terminal: bool = False,
        agent_count: Optional[str] = None,
        messages: Optional[List[Dict]] = None
    ):
        """
        添加 Code Agent 轮次记录
        
        Args:
            agent_id: Agent ID (原始 agent_id，已废弃，使用 agent_count)
            round_num: 轮次编号
            model_start_time: 模型调用开始时间
            model_end_time: 模型调用结束时间
            response: 模型响应
            actions: 动作列表
            code: 执行的代码
            action_start_time: 动作执行开始时间
            action_end_time: 动作执行结束时间
            action_result: 执行结果
            is_terminal: 是否是终止动作
            agent_count: 唯一标识符，推荐使用
            messages: 对话消息列表
        """
        # 如果提供了 agent_count，优先使用它；否则使用 agent_id（向后兼容）
        lookup_id = agent_count if agent_count else agent_id
        
        if lookup_id not in self.agents:
            # 如果没有找到，尝试使用 agent_id 创建一个新的
            self.add_code_agent(agent_id, "Unknown task")
            lookup_id = list(self.agents.keys())[-1]  # 获取刚创建的 agent_count
        
        # 更新完整的 messages（如果提供），过滤掉system消息
        if messages:
            self.agents[lookup_id]["full_messages"] = [msg for msg in messages if msg.get('role') != 'system']
            # Code Agent: 保存从最后一个 assistant 开始的所有消息
            # 包括 assistant (with tool_calls) + tool + user
            filtered_messages = [msg for msg in messages if msg.get('role') != 'system']
            # 找到最后一个 assistant 消息的位置
            last_assistant_idx = -1
            for i in range(len(filtered_messages) - 1, -1, -1):
                if filtered_messages[i].get('role') == 'assistant':
                    last_assistant_idx = i
                    break
            # 从最后一个 assistant 开始保存所有后续消息
            if last_assistant_idx >= 0:
                new_messages = filtered_messages[last_assistant_idx:]
            else:
                new_messages = filtered_messages[-3:] if len(filtered_messages) >= 3 else filtered_messages
        else:
            new_messages = []
        
        model_duration = model_end_time - model_start_time
        
        # 构建 model_prediction
        model_prediction = {
            "time_span": {
                "start_timestamp": model_start_time,
                "start_time_iso": datetime.fromtimestamp(model_start_time, tz=timezone.utc).isoformat(),
                "end_timestamp": model_end_time,
                "end_time_iso": datetime.fromtimestamp(model_end_time, tz=timezone.utc).isoformat(),
                "duration": model_duration
            },
            "response": response,
            "actions": actions,
            "messages": new_messages
        }
        
        if is_terminal:
            model_prediction["special_action"] = "DONE"
        
        round_data = {
            "round_id": round_num,
            "model_prediction": model_prediction,
            "action_execution": None,
            "total_duration": model_duration
        }
        
        if action_start_time and action_end_time and not is_terminal:
            action_duration = action_end_time - action_start_time
            round_data["action_execution"] = {
                "time_span": {
                    "start_timestamp": action_start_time,
                    "start_time_iso": datetime.fromtimestamp(action_start_time, tz=timezone.utc).isoformat(),
                    "end_timestamp": action_end_time,
                    "end_time_iso": datetime.fromtimestamp(action_end_time, tz=timezone.utc).isoformat(),
                    "duration": action_duration
                },
                "code": code,
                "result": action_result or {
                    "status": "success",
                    "returncode": 0,
                    "output": "",
                    "error": ""
                }
            }
            round_data["total_duration"] = model_duration + action_duration
        
        self.agents[lookup_id]["rounds"].append(round_data)
        
    def finish_code_agent(self, agent_id: str, final_status: str = "success", agent_count: Optional[str] = None):
        """
        完成 Code Agent 记录
        
        Args:
            agent_id: Agent ID (原始 agent_id，已废弃，使用 agent_count)
            final_status: 最终状态 ("success", "failed", "timeout")
            agent_count: 唯一标识符，推荐使用
        """
        lookup_id = agent_count if agent_count else agent_id
        
        if lookup_id not in self.agents:
            return
            
        agent = self.agents[lookup_id]
        end_time = time.time()
        
        # 更新 recording
        agent["recording"]["end_timestamp"] = end_time
        agent["recording"]["end_time_iso"] = datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat()
        agent["recording"]["duration"] = end_time - agent["recording"]["start_timestamp"]
        
        # 计算 summary
        rounds = agent["rounds"]
        total_rounds = len(rounds)
        rounds_with_action = sum(1 for r in rounds if r.get("action_execution"))
        
        total_model_time = sum(
            r["model_prediction"]["time_span"]["duration"] 
            for r in rounds
        )
        total_action_time = sum(
            r["action_execution"]["time_span"]["duration"] 
            for r in rounds 
            if r.get("action_execution")
        )
        
        agent["summary"] = {
            "total_rounds": total_rounds,
            "rounds_with_action": rounds_with_action,
            "total_model_time": round(total_model_time, 3),
            "total_action_time": round(total_action_time, 3),
            "average_model_time": round(total_model_time / total_rounds, 3) if total_rounds > 0 else 0,
            "average_action_time": round(total_action_time / rounds_with_action, 3) if rounds_with_action > 0 else 0,
            "final_status": final_status
        }
    
    # =====================================================================
    # GUI Agent 相关方法
    # =====================================================================
    
    def add_gui_agent(
        self,
        agent_id: str,
        task: str,
        model_name: str = "gpt-5",
        parent_round: int = 0,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        device_id: Optional[str] = None,
        system_prompt: str = ""
    ) -> str:
        """
        添加 GUI Agent 的一次新调用
        
        Args:
            agent_id: Agent ID (如 "gui_agent_1")
            task: 分配的任务
            model_name: 使用的模型
            parent_round: 父轮次 (Plan Agent 的哪一轮调度的)
            start_timestamp: 开始时间戳（可选）
            end_timestamp: 结束时间戳（可选）
            device_id: 设备 ID
            system_prompt: GUI Agent的system prompt
            
        Returns:
            agent_count: 本次调用的唯一标识符
        """
        start_ts = start_timestamp if start_timestamp else time.time()
        end_ts = end_timestamp if end_timestamp else 0.0
        duration = (end_ts - start_ts) if end_ts > 0 else 0.0
        
        agent_type = "gui_agent"
        
        # 全局计数
        if agent_type not in self._agent_global_counts:
            self._agent_global_counts[agent_type] = 0
        self._agent_global_counts[agent_type] += 1
        global_count = self._agent_global_counts[agent_type]
        
        # 本地计数
        device = device_id or self.device_id
        local_key = (device, agent_id)
        if local_key not in self._agent_local_counts:
            self._agent_local_counts[local_key] = 0
        self._agent_local_counts[local_key] += 1
        call_id = self._agent_local_counts[local_key]
        
        agent_count = f"{agent_type}_call_{global_count}"
        
        self.agents[agent_count] = {
            "agent_id": agent_id,
            "agent_count": agent_count,
            "call_id": call_id,
            "type": "gui",
            "task": task,
            "model_name": model_name,
            "system_prompt": system_prompt,
            "parent_agent": "coordinator",
            "parent_round": parent_round,
            "device_id": device,
            "recording": {
                "start_timestamp": start_ts,
                "start_time_iso": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                "end_timestamp": end_ts,
                "end_time_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat() if end_ts > 0 else "",
                "duration": duration
            },
            "rounds": [],
            "summary": None,
            "full_messages": []
        }
        
        return agent_count
    
    def add_gui_agent_round(
        self,
        agent_id: str,
        round_num: int,
        model_start_time: float,
        model_end_time: float,
        response: str,
        actions: List[Dict],
        action_start_time: Optional[float] = None,
        action_end_time: Optional[float] = None,
        action_result: Optional[Dict] = None,
        is_terminal: bool = False,
        agent_count: Optional[str] = None,
        messages: Optional[List[Dict]] = None,
        screenshot_url: Optional[str] = None,
        timing: Optional[Dict] = None,
        pyautogui_code: Optional[str] = None
    ):
        """
        添加 GUI Agent 轮次记录
        
        Args:
            agent_id: Agent ID
            round_num: 轮次编号
            model_start_time: 模型调用开始时间
            model_end_time: 模型调用结束时间
            response: 模型响应
            actions: 动作列表
            action_start_time: 动作执行开始时间
            action_end_time: 动作执行结束时间
            action_result: 执行结果
            is_terminal: 是否是终止动作
            agent_count: 唯一标识符
            messages: 对话消息列表
            screenshot_url: 截图URL路径
            timing: 详细的timing信息（preparation, api_call, total_round）
            pyautogui_code: PyAutoGUI 代码
        """
        lookup_id = agent_count if agent_count else agent_id
        
        if lookup_id not in self.agents:
            self.add_gui_agent(agent_id, "Unknown task")
            lookup_id = list(self.agents.keys())[-1]
        
        # 更新完整的 messages
        if messages:
            # ⭐ 关键步骤：从 PyAutoGUI 代码中提取真实坐标，更新 messages
            # 这确保 JSON 中记录的是最终执行的真实坐标（1920x1080）
            if pyautogui_code:
                messages = self._update_coordinates_from_pyautogui(messages, pyautogui_code)
            
            self.agents[lookup_id]["full_messages"] = [msg for msg in messages if msg.get('role') != 'system']
            # GUI Agent: 保存从最后一个 assistant 开始的所有消息
            # 包括 assistant (with tool_calls) + tool + user
            filtered_messages = [msg for msg in messages if msg.get('role') != 'system']
            # 找到最后一个 assistant 消息的位置
            last_assistant_idx = -1
            for i in range(len(filtered_messages) - 1, -1, -1):
                if filtered_messages[i].get('role') == 'assistant':
                    last_assistant_idx = i
                    break
            # 从最后一个 assistant 开始保存所有后续消息
            if last_assistant_idx >= 0:
                new_messages = filtered_messages[last_assistant_idx:]
            else:
                new_messages = filtered_messages[-3:] if len(filtered_messages) >= 3 else filtered_messages
        else:
            new_messages = []
        
        model_duration = model_end_time - model_start_time
        
        # 构建 model_prediction
        model_prediction = {
            "time_span": {
                "start_timestamp": model_start_time,
                "start_time_iso": datetime.fromtimestamp(model_start_time, tz=timezone.utc).isoformat(),
                "end_timestamp": model_end_time,
                "end_time_iso": datetime.fromtimestamp(model_end_time, tz=timezone.utc).isoformat(),
                "duration": model_duration
            },
            "response": response,
            "actions": actions,
            "messages": new_messages,
            "screenshot_url": screenshot_url,
            "pyautogui_code": pyautogui_code
        }
        
        # 添加详细的 timing 信息（如果提供）
        if timing:
            model_prediction["timing"] = {
                "preparation": {
                    "duration": timing.get("preparation_time", 0.0)
                },
                "api_call": {
                    "duration": timing.get("api_call_time", 0.0)
                },
                "total_round": {
                    "duration": timing.get("total_round_time", 0.0)
                }
            }
        
        if is_terminal:
            model_prediction["special_action"] = "DONE"
        
        round_data = {
            "round_id": round_num,
            "model_prediction": model_prediction,
            "action_execution": None,
            "total_duration": model_duration
        }
        
        if action_start_time and action_end_time and not is_terminal:
            # 计算 action_duration
            if timing:
                total_round_time = timing.get("total_round_time", 0.0)
                prep_time = timing.get("preparation_time", 0.0)
                api_time = timing.get("api_call_time", 0.0)
                action_duration = total_round_time - prep_time - api_time
                if action_duration < 0:
                    print(f"⚠️ WARNING: action_duration is negative ({action_duration:.3f}s)")
                    action_duration = 0.0
            else:
                action_duration = action_end_time - action_start_time
            
            round_data["action_execution"] = {
                "time_span": {
                    "start_timestamp": action_start_time,
                    "start_time_iso": datetime.fromtimestamp(action_start_time, tz=timezone.utc).isoformat(),
                    "end_timestamp": action_end_time,
                    "end_time_iso": datetime.fromtimestamp(action_end_time, tz=timezone.utc).isoformat(),
                    "duration": action_duration
                },
                "result": action_result or {
                    "status": "success",
                    "returncode": 0,
                    "output": "",
                    "error": ""
                }
            }
            # 设置 total_duration，优先使用 timing 中的值（如果有）
            if timing:
                round_data["total_duration"] = timing.get("total_round_time", model_duration + action_duration)
            else:
                round_data["total_duration"] = model_duration + action_duration
        
        self.agents[lookup_id]["rounds"].append(round_data)
    
    def finish_gui_agent(self, agent_id: str, final_status: str = "success", agent_count: Optional[str] = None):
        """
        完成 GUI Agent 记录（与finish_code_agent相同逻辑）
        
        Args:
            agent_id: Agent ID
            final_status: 最终状态
            agent_count: 唯一标识符
        """
        self.finish_code_agent(agent_id, final_status, agent_count)
    
    # =====================================================================
    # 坐标提取和更新方法（用于确保 JSON 中记录的是真实的 PyAutoGUI 坐标）
    # =====================================================================
    
    def _extract_coordinates_from_pyautogui(self, code: str) -> Dict[str, Any]:
        """
        从 PyAutoGUI 代码中提取真实坐标
        
        支持的操作格式：
        - pyautogui.click(x, y, button='left/right/middle')
        - pyautogui.doubleClick(x, y)
        - pyautogui.moveTo(x, y)
        - pyautogui.dragTo(x, y, duration=...)
        - pyautogui.scroll(pixels, x=x, y=y)
        - pyautogui.scroll(pixels)  # 无坐标
        
        Args:
            code: PyAutoGUI 代码字符串
            
        Returns:
            包含 action 和 coordinate 的字典，如：
            {'action': 'left_click', 'coordinate': [100, 200]}
            如果提取失败，返回空字典
        """
        if not code:
            return {}
        
        # 匹配 pyautogui.click(x, y, button='...')
        click_pattern = r'pyautogui\.click\((\d+),\s*(\d+)(?:,\s*button=[\'"]([\w]+)[\'"])?'
        click_match = re.search(click_pattern, code)
        if click_match:
            x, y, button = click_match.groups()
            button = button or 'left'
            action_name = f'{button}_click'
            return {
                'action': action_name,
                'coordinate': [int(x), int(y)]
            }
        
        # 匹配 pyautogui.doubleClick(x, y)
        double_click_pattern = r'pyautogui\.doubleClick\((\d+),\s*(\d+)\)'
        double_click_match = re.search(double_click_pattern, code)
        if double_click_match:
            x, y = double_click_match.groups()
            return {
                'action': 'double_click',
                'coordinate': [int(x), int(y)]
            }
        
        # 匹配 pyautogui.moveTo(x, y)
        move_pattern = r'pyautogui\.moveTo\((\d+),\s*(\d+)\)'
        move_match = re.search(move_pattern, code)
        if move_match:
            x, y = move_match.groups()
            return {
                'action': 'mouse_move',
                'coordinate': [int(x), int(y)]
            }
        
        # 匹配 pyautogui.dragTo(x, y, duration=...)
        drag_pattern = r'pyautogui\.dragTo\((\d+),\s*(\d+)'
        drag_match = re.search(drag_pattern, code)
        if drag_match:
            x, y = drag_match.groups()
            return {
                'action': 'left_click_drag',
                'coordinate': [int(x), int(y)]
            }
        
        # 匹配 pyautogui.scroll(pixels, x=x, y=y)
        scroll_with_coord_pattern = r'pyautogui\.scroll\([^,]+,\s*x=(\d+),\s*y=(\d+)\)'
        scroll_with_coord_match = re.search(scroll_with_coord_pattern, code)
        if scroll_with_coord_match:
            x, y = scroll_with_coord_match.groups()
            return {
                'action': 'scroll',
                'coordinate': [int(x), int(y)]
            }
        
        # 匹配 pyautogui.scroll(pixels) - 无坐标，使用屏幕中心
        scroll_pattern = r'pyautogui\.scroll\([^)]+\)'
        scroll_match = re.search(scroll_pattern, code)
        if scroll_match:
            # scroll 操作可能没有坐标，使用屏幕中心作为默认值
            return {
                'action': 'scroll',
                'coordinate': [960, 540]  # 1920x1080 的中心
            }
        
        return {}
    
    def _update_coordinates_from_pyautogui(self, messages: List[Dict], pyautogui_code: str) -> List[Dict]:
        """
        从 PyAutoGUI 代码中提取真实坐标，更新 messages 中的 tool_calls 坐标
        
        这确保了 JSON 中记录的坐标是最终执行的真实坐标（1920x1080），
        而不是模型的原始输出坐标（可能是相对坐标或缩放图坐标）。
        
        Args:
            messages: 原始 messages 列表
            pyautogui_code: PyAutoGUI 执行代码
            
        Returns:
            更新坐标后的 messages 列表（深拷贝）
        """
        if not pyautogui_code or not messages:
            return messages
        
        # 从 PyAutoGUI 代码提取真实坐标
        extracted = self._extract_coordinates_from_pyautogui(pyautogui_code)
        if not extracted:
            return messages
        
        # 深拷贝 messages，避免修改原始数据
        import copy
        updated_messages = copy.deepcopy(messages)
        
        # 更新 messages 中的 tool_calls
        for msg in updated_messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for tool_call in msg['tool_calls']:
                    func_name = tool_call.get('function', {}).get('name', '')
                    if func_name in ['computer_use', 'computer']:
                        try:
                            args = json.loads(tool_call['function']['arguments'])
                            current_action = args.get('action')
                            
                            # 只更新匹配的 action 类型的坐标
                            if current_action == extracted['action']:
                                args['coordinate'] = extracted['coordinate']
                                tool_call['function']['arguments'] = json.dumps(args)
                        except (json.JSONDecodeError, KeyError) as e:
                            # 如果解析失败，保持原样
                            pass
        
        return updated_messages
    
    # =====================================================================
    # 输出方法
    # =====================================================================
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式 (符合 record_template.py v2.0.0 规范)
        
        输出格式：
        {
            "version": "2.0.0",
            "task_id": "...",
            "instruction": "...",
            "metadata": TimeSpan格式,
            "plan_agent": {...},  // 替代 coordinator
            "devices": [...],
            "summary": {...}
        }
        
        Returns:
            完整的记录字典
        """
        total_duration = self.end_timestamp - self.start_timestamp if self.end_timestamp else 0
        
        # 构建 Plan Agent summary
        plan_agent_rounds_with_action = sum(1 for r in self.plan_agent_rounds if r.get("action_execution"))
        plan_agent_model_time = sum(
            r["model_prediction"]["time_span"]["duration"] 
            for r in self.plan_agent_rounds
        )
        plan_agent_action_time = sum(
            r["action_execution"]["time_span"]["duration"] 
            for r in self.plan_agent_rounds 
            if r.get("action_execution")
        )
        
        plan_agent = {
            "agent_id": "coordinator",
            "type": "plan",  # v2 使用 "plan" 而不是 "planner"
            "model_name": self.plan_agent_model,
            "system_prompt": self.plan_agent_system_prompt,
            "rounds": self.plan_agent_rounds,
            "summary": {
                "total_rounds": len(self.plan_agent_rounds),
                "rounds_with_action": plan_agent_rounds_with_action,
                "total_model_time": round(plan_agent_model_time, 3),
                "total_action_time": round(plan_agent_action_time, 3),
                "average_model_time": round(plan_agent_model_time / len(self.plan_agent_rounds), 3) if self.plan_agent_rounds else 0,
                "average_action_time": round(plan_agent_action_time / plan_agent_rounds_with_action, 3) if plan_agent_rounds_with_action > 0 else 0
            }
        }
        
        # 构建 Devices - 按 device_id 分组
        devices = []
        if self.agents:
            device_agents_map = {}
            for agent in self.agents.values():
                dev_id = agent.get("device_id", self.device_id)
                if dev_id not in device_agents_map:
                    device_agents_map[dev_id] = []
                device_agents_map[dev_id].append(agent)
            
            for dev_id in sorted(device_agents_map.keys()):
                devices.append({
                    "device_id": dev_id,
                    "type": self.device_type,
                    "metadata": {},
                    "agents": device_agents_map[dev_id]
                })
        
        # 计算全局统计
        total_model_time = plan_agent_model_time
        total_action_time = plan_agent_action_time
        total_rounds = len(self.plan_agent_rounds)
        
        for agent in self.agents.values():
            if agent.get("summary"):
                total_model_time += agent["summary"]["total_model_time"]
                total_action_time += agent["summary"]["total_action_time"]
                total_rounds += agent["summary"]["total_rounds"]
        
        # 判断是否成功
        if self._overall_success is not None:
            all_success = self._overall_success
        else:
            if len(self.plan_agent_rounds) > 0:
                last_round = self.plan_agent_rounds[-1]
                if last_round.get("action_execution"):
                    dispatched_agents = last_round["action_execution"].get("result", {}).get("dispatched_agents", [])
                    if dispatched_agents:
                        round_num = last_round.get("round_id", 0)
                        round_agents = [a for a in self.agents.values() if a.get("parent_round") == round_num]
                        if round_agents:
                            all_success = all(a.get("summary", {}).get("final_status") == "success" for a in round_agents)
                        else:
                            all_success = True
                    else:
                        all_success = True
                else:
                    all_success = True
            else:
                all_success = False
        
        return {
            "version": self.VERSION,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "metadata": {
                "start_timestamp": self.start_timestamp,
                "start_time_iso": self.start_time_iso,
                "end_timestamp": self.end_timestamp,
                "end_time_iso": self.end_time_iso,
                "duration": round(total_duration, 3)  # v2 使用 duration 而不是 total_duration
            },
            "plan_agent": plan_agent,  # v2 使用 plan_agent 而不是 coordinator
            "devices": devices,
            "summary": {
                "total_duration": round(total_duration, 3),
                "total_model_time": round(total_model_time, 3),
                "total_action_time": round(total_action_time, 3),
                "coordinator_rounds": len(self.plan_agent_rounds),
                "devices_count": len(devices),
                "total_agents_count": len(self.agents),
                "total_rounds": total_rounds,
                "success": all_success,
                "final_answer": self.final_answer
            }
        }
    
    def save(self, file_path: str, indent: int = 2):
        """
        保存到 JSON 文件
        
        Args:
            file_path: 输出文件路径
            indent: JSON 缩进
            
        Returns:
            str: 保存的文件路径
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=indent)
        print(f"✓ Execution record saved: {file_path}")
        return file_path
    
    def save_to_file(self, file_path: str, indent: int = 2):
        """
        save() 方法的别名，保持向后兼容
        
        Args:
            file_path: 输出文件路径
            indent: JSON 缩进
        
        Returns:
            str: 保存的文件路径
        """
        return self.save(file_path, indent)


# ============== 便捷函数 ==============

def save_execution_record(
    execution_log: Dict[str, Any],
    output_path: str,
    task_id: str = "",
    instruction: str = "",
    model_name: str = "gpt-5"
):
    """
    便捷函数：直接从 execution_log 保存为新格式
    
    Args:
        execution_log: 原有的 execution_log
        output_path: 输出文件路径
        task_id: 任务ID
        instruction: 用户指令
        model_name: 模型名称
        
    Returns:
        Dict: 转换后的记录字典
    """
    recorder = ExecutionRecorder(
        task_id=task_id or f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        instruction=instruction or execution_log.get("task", ""),
        coordinator_model=model_name
    )
    
    # 设置时间
    recorder.start_timestamp = execution_log.get("start_timestamp", time.time())
    recorder.start_time_iso = execution_log.get("start_time", datetime.now(timezone.utc).isoformat())
    recorder.end_timestamp = execution_log.get("end_timestamp", time.time())
    recorder.end_time_iso = execution_log.get("end_time", datetime.now(timezone.utc).isoformat())
    
    recorder.save(output_path)
    return recorder.to_dict()


# ============== 使用示例 ==============

def usage_example():
    """
    使用示例：展示如何使用 ExecutionRecorder V2
    """
    import time
    
    # 1. 初始化记录器
    recorder = ExecutionRecorder(
        task_id="demo_task_001",
        instruction="在美团和饿了么上搜索喜茶的价格，比较哪个更便宜",
        coordinator_model="gpt-4o",
        coordinator_system_prompt="你是一个任务协调者..."
    )
    
    # 2. 开始记录
    recorder.start()
    
    # 3. 记录 Plan Agent 第一轮
    t1 = time.time()
    recorder.add_plan_agent_round(
        round_num=0,
        model_start_time=t1,
        model_end_time=t1 + 2.5,
        response="我将任务分解为两个并行子任务...",
        actions=[
            {"type": "dispatch_agents", "data": {"agents": ["gui_agent_1", "gui_agent_2"]}}
        ],
        action_start_time=t1 + 2.5,
        action_end_time=t1 + 3.0,
        dispatched_agents=["gui_agent_1", "gui_agent_2"],
        dependencies={
            "gui_agent_1": {
                "round": 0,
                "task": "在美团上搜索喜茶",
                "depends_on": []
            },
            "gui_agent_2": {
                "round": 0,
                "task": "在饿了么上搜索喜茶",
                "depends_on": []
            }
        }
    )
    
    # 4. 记录 GUI Agent 1
    agent_count_1 = recorder.add_gui_agent(
        agent_id="gui_agent",
        task="在美团上搜索喜茶",
        model_name="gpt-4o",
        parent_round=0,
        device_id="Desktop-1"
    )
    
    t2 = time.time()
    recorder.add_gui_agent_round(
        agent_id="gui_agent",
        round_num=1,
        model_start_time=t2,
        model_end_time=t2 + 1.5,
        response="打开美团网页",
        actions=[{"type": "click", "data": {"x": 100, "y": 200}}],
        action_start_time=t2 + 1.5,
        action_end_time=t2 + 2.0,
        agent_count=agent_count_1,
        screenshot_url="/screenshots/gui1_r1.png"
    )
    
    recorder.finish_gui_agent("gui_agent", "success", agent_count_1)
    
    # 5. 记录 GUI Agent 2
    agent_count_2 = recorder.add_gui_agent(
        agent_id="gui_agent",
        task="在饿了么上搜索喜茶",
        model_name="gpt-4o",
        parent_round=0,
        device_id="Desktop-2"
    )
    
    t3 = time.time()
    recorder.add_gui_agent_round(
        agent_id="gui_agent",
        round_num=1,
        model_start_time=t3,
        model_end_time=t3 + 1.5,
        response="打开饿了么网页",
        actions=[{"type": "click", "data": {"x": 100, "y": 200}}],
        action_start_time=t3 + 1.5,
        action_end_time=t3 + 2.0,
        agent_count=agent_count_2,
        screenshot_url="/screenshots/gui2_r1.png"
    )
    
    recorder.finish_gui_agent("gui_agent", "success", agent_count_2)
    
    # 6. 结束并保存
    recorder.finish_task(success=True)
    recorder.save("usage_example_v2_output.json")
    
    print("✅ 示例记录完成！")
    return recorder.to_dict()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "example":
        usage_example()
    else:
        print("ExecutionRecorder V2 - 执行记录器")
        print("=" * 50)
        print("\n用法:")
        print("  python execution_recorder_v2.py example  # 运行使用示例")
