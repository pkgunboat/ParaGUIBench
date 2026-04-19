from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import json

@dataclass
class TimeMetadata:
    """
    时间元数据类，用于记录操作的起止时间和持续时长。
    """
    start_timestamp: float
    start_time_iso: str
    end_timestamp: float
    end_time_iso: str
    duration: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TimeMetadata':
        """
        从字典创建 TimeMetadata 实例。

        Args:
            data (Dict[str, Any]): 包含时间数据的字典。

        Returns:
            TimeMetadata: 实例对象。
        """
        return cls(
            start_timestamp=data.get("start_timestamp", 0.0),
            start_time_iso=data.get("start_time_iso", ""),
            end_timestamp=data.get("end_timestamp", 0.0),
            end_time_iso=data.get("end_time_iso", ""),
            duration=data.get("duration", 0.0)
        )

@dataclass
class Recording(TimeMetadata):
    """
    录制信息类，继承自 TimeMetadata，可能包含录制文件路径。
    """
    path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Recording':
        """
        从字典创建 Recording 实例。
        
        Args:
            data (Dict[str, Any]): 包含录制信息的字典。
            
        Returns:
            Recording: 实例对象。
        """
        obj = super().from_dict(data)
        return cls(
            start_timestamp=obj.start_timestamp,
            start_time_iso=obj.start_time_iso,
            end_timestamp=obj.end_timestamp,
            end_time_iso=obj.end_time_iso,
            duration=obj.duration,
            path=data.get("path")
        )

@dataclass
class Action:
    """
    动作定义类，描述智能体执行的具体动作。
    """
    type: str
    # 动态字段，根据 type 不同而不同，例如 agent_id, task, coordinate, text 等
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Action':
        """
        从字典创建 Action 实例。
        
        Args:
            data (Dict[str, Any]): 包含动作信息的字典。
            
        Returns:
            Action: 实例对象。
        """
        action_type = data.get("type", "")
        # 提取除 type 以外的所有字段作为参数
        params = {k: v for k, v in data.items() if k != "type"}
        return cls(type=action_type, params=params)

@dataclass
class ModelPrediction:
    """
    模型预测类，记录大模型的输入输出及解析结果。
    """
    start_timestamp: float
    start_time_iso: str
    end_timestamp: float
    end_time_iso: str
    duration: float
    response: str
    actions: List[Action]
    pyautogui_code: Optional[str] = None
    special_action: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelPrediction':
        """
        从字典创建 ModelPrediction 实例。
        
        Args:
            data (Dict[str, Any]): 包含模型预测信息的字典。
            
        Returns:
            ModelPrediction: 实例对象。
        """
        return cls(
            start_timestamp=data.get("start_timestamp", 0.0),
            start_time_iso=data.get("start_time_iso", ""),
            end_timestamp=data.get("end_timestamp", 0.0),
            end_time_iso=data.get("end_time_iso", ""),
            duration=data.get("duration", 0.0),
            response=data.get("response", ""),
            actions=[Action.from_dict(a) for a in data.get("actions", [])],
            pyautogui_code=data.get("pyautogui_code"),
            special_action=data.get("special_action")
        )

@dataclass
class ExecutionResult:
    """
    执行结果类，记录动作执行后的返回状态和输出。
    """
    status: str
    returncode: int
    output: str
    error: str
    dispatched_agents: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionResult':
        """
        从字典创建 ExecutionResult 实例。
        
        Args:
            data (Dict[str, Any]): 包含执行结果的字典。
            
        Returns:
            ExecutionResult: 实例对象。
        """
        return cls(
            status=data.get("status", ""),
            returncode=data.get("returncode", 0),
            output=data.get("output", ""),
            error=data.get("error", ""),
            dispatched_agents=data.get("dispatched_agents")
        )

@dataclass
class ActionExecution:
    """
    动作执行过程类，包含时间信息和执行结果。
    """
    start_timestamp: float
    start_time_iso: str
    end_timestamp: float
    end_time_iso: str
    duration: float
    result: Optional[ExecutionResult] = None
    code: Optional[str] = None  # 实际执行的代码快照

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionExecution':
        """
        从字典创建 ActionExecution 实例。
        
        Args:
            data (Dict[str, Any]): 包含动作执行过程信息的字典。
            
        Returns:
            ActionExecution: 实例对象。
        """
        result_data = data.get("result")
        return cls(
            start_timestamp=data.get("start_timestamp", 0.0),
            start_time_iso=data.get("start_time_iso", ""),
            end_timestamp=data.get("end_timestamp", 0.0),
            end_time_iso=data.get("end_time_iso", ""),
            duration=data.get("duration", 0.0),
            result=ExecutionResult.from_dict(result_data) if result_data else None,
            code=data.get("code")
        )

@dataclass
class Round:
    """
    交互轮次类，包含一次完整的 模型预测 -> 动作执行 循环。
    """
    round_id: int
    model_prediction: ModelPrediction
    action_execution: Optional[ActionExecution]
    total_duration: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Round':
        """
        从字典创建 Round 实例。
        
        Args:
            data (Dict[str, Any]): 包含轮次信息的字典。
            
        Returns:
            Round: 实例对象。
        """
        ae_data = data.get("action_execution")
        return cls(
            round_id=data.get("round", 0),
            model_prediction=ModelPrediction.from_dict(data.get("model_prediction", {})),
            action_execution=ActionExecution.from_dict(ae_data) if ae_data else None,
            total_duration=data.get("total_duration", 0.0)
        )

@dataclass
class AgentSummary:
    """
    智能体执行摘要类，统计轮次和时间消耗。
    """
    total_rounds: int
    rounds_with_action: int
    total_model_time: float
    total_action_time: float
    average_model_time: float
    average_action_time: float
    final_status: Optional[str] = None
    dispatched_gui_agents: Optional[int] = None
    dispatched_code_agents: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentSummary':
        """
        从字典创建 AgentSummary 实例。
        
        Args:
            data (Dict[str, Any]): 包含摘要信息的字典。
            
        Returns:
            AgentSummary: 实例对象。
        """
        return cls(
            total_rounds=data.get("total_rounds", 0),
            rounds_with_action=data.get("rounds_with_action", 0),
            total_model_time=data.get("total_model_time", 0.0),
            total_action_time=data.get("total_action_time", 0.0),
            average_model_time=data.get("average_model_time", 0.0),
            average_action_time=data.get("average_action_time", 0.0),
            final_status=data.get("final_status"),
            dispatched_gui_agents=data.get("dispatched_gui_agents"),
            dispatched_code_agents=data.get("dispatched_code_agents")
        )

@dataclass
class BaseAgent:
    """
    智能体基类，包含通用的记录和轮次信息。
    """
    recording: Recording
    rounds: List[Round]
    summary: AgentSummary

@dataclass
class PlanAgent(BaseAgent):
    """
    规划智能体类，负责任务分解。
    """
    device_id: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlanAgent':
        """
        从字典创建 PlanAgent 实例。
        
        Args:
            data (Dict[str, Any]): 包含规划智能体信息的字典。
            
        Returns:
            PlanAgent: 实例对象。
        """
        return cls(
            recording=Recording.from_dict(data.get("recording", {})),
            rounds=[Round.from_dict(r) for r in data.get("rounds", [])],
            summary=AgentSummary.from_dict(data.get("summary", {})),
            device_id=data.get("device_id")
        )

@dataclass
class GuiAgent(BaseAgent):
    """
    GUI 智能体类，负责界面操作。
    """
    agent_id: str
    task: str
    parent_agent: str
    parent_round: int
    device_id: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GuiAgent':
        """
        从字典创建 GuiAgent 实例。
        
        Args:
            data (Dict[str, Any]): 包含 GUI 智能体信息的字典。
            
        Returns:
            GuiAgent: 实例对象。
        """
        return cls(
            agent_id=data.get("agent_id", ""),
            task=data.get("task", ""),
            parent_agent=data.get("parent_agent", ""),
            parent_round=data.get("parent_round", 0),
            recording=Recording.from_dict(data.get("recording", {})),
            rounds=[Round.from_dict(r) for r in data.get("rounds", [])],
            summary=AgentSummary.from_dict(data.get("summary", {})),
            device_id=data.get("device_id", [])
        )

@dataclass
class CodeAgent(BaseAgent):
    """
    代码智能体类，负责代码执行。
    """
    agent_id: str
    task: str
    code_type: str
    parent_agent: str
    parent_round: int
    device_id: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodeAgent':
        """
        从字典创建 CodeAgent 实例。
        
        Args:
            data (Dict[str, Any]): 包含代码智能体信息的字典。
            
        Returns:
            CodeAgent: 实例对象。
        """
        return cls(
            agent_id=data.get("agent_id", ""),
            task=data.get("task", ""),
            code_type=data.get("code_type", ""),
            parent_agent=data.get("parent_agent", ""),
            parent_round=data.get("parent_round", 0),
            recording=Recording.from_dict(data.get("recording", {})),
            rounds=[Round.from_dict(r) for r in data.get("rounds", [])],
            summary=AgentSummary.from_dict(data.get("summary", {})),
            device_id=data.get("device_id", [])
        )

@dataclass
class AgentsCollection:
    """
    智能体集合类，包含所有类型的智能体。
    """
    plan_agent: PlanAgent
    gui_agents: List[GuiAgent]
    code_agents: List[CodeAgent]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentsCollection':
        """
        从字典创建 AgentsCollection 实例。
        
        Args:
            data (Dict[str, Any]): 包含所有智能体信息的字典。
            
        Returns:
            AgentsCollection: 实例对象。
        """
        return cls(
            plan_agent=PlanAgent.from_dict(data.get("plan_agent", {})),
            gui_agents=[GuiAgent.from_dict(g) for g in data.get("gui_agents", [])],
            code_agents=[CodeAgent.from_dict(c) for c in data.get("code_agents", [])]
        )

@dataclass
class GlobalSummary:
    """
    全局统计信息类。
    """
    total_duration: float
    total_model_time: float
    total_action_time: float
    plan_agent_rounds: int
    gui_agents_count: int
    code_agents_count: int
    total_gui_rounds: int
    total_code_rounds: int
    success: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GlobalSummary':
        """
        从字典创建 GlobalSummary 实例。
        
        Args:
            data (Dict[str, Any]): 包含全局统计信息的字典。
            
        Returns:
            GlobalSummary: 实例对象。
        """
        return cls(
            total_duration=data.get("total_duration", 0.0),
            total_model_time=data.get("total_model_time", 0.0),
            total_action_time=data.get("total_action_time", 0.0),
            plan_agent_rounds=data.get("plan_agent_rounds", 0),
            gui_agents_count=data.get("gui_agents_count", 0),
            code_agents_count=data.get("code_agents_count", 0),
            total_gui_rounds=data.get("total_gui_rounds", 0),
            total_code_rounds=data.get("total_code_rounds", 0),
            success=data.get("success", False)
        )

@dataclass
class RecordTemplate:
    """
    顶层记录模板类，对应整个 JSON 文件的结构。
    """
    version: str
    task_id: str
    instruction: str
    metadata: TimeMetadata
    agents: AgentsCollection
    summary: GlobalSummary

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecordTemplate':
        """
        从字典创建 RecordTemplate 实例。
        
        Args:
            data (Dict[str, Any]): 包含完整记录的字典。
            
        Returns:
            RecordTemplate: 实例对象。
        """
        return cls(
            version=data.get("version", "1.0.0"),
            task_id=data.get("task_id", ""),
            instruction=data.get("instruction", ""),
            metadata=TimeMetadata.from_dict(data.get("metadata", {})),
            agents=AgentsCollection.from_dict(data.get("agents", {})),
            summary=GlobalSummary.from_dict(data.get("summary", {}))
        )

    @classmethod
    def load_from_file(cls, file_path: str) -> 'RecordTemplate':
        """
        从 JSON 文件加载记录。
        
        Args:
            file_path (str): JSON 文件路径。
            
        Returns:
            RecordTemplate: 实例对象。
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        """
        将 RecordTemplate 实例转换为字典。
        
        Returns:
            Dict[str, Any]: 包含完整记录的字典。
        """
        return asdict(self)

    def save_to_file(self, file_path: str, indent: int = 2) -> None:
        """
        将 RecordTemplate 实例保存为 JSON 文件。
        
        Args:
            file_path (str): 要保存的 JSON 文件路径。
            indent (int): JSON 文件的缩进空格数，默认为 2。
            
        Returns:
            None
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=indent)
