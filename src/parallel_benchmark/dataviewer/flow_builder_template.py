import json
from typing import List, Dict, Optional, Union
from enum import Enum
import datetime

# ==========================================
# 基础数据结构定义
# ==========================================

class AgentType(Enum):
    PLANNER = "planner"
    GUI_AGENT = "gui_agent"
    CODE_AGENT = "code_agent"

class EdgeType(Enum):
    CONTROL = "control"       # 仅仅是控制流，任务触发
    DATA_FLOW = "data_flow"   # 数据流，有文件或信息传递

class TaskNode:
    """
    代表流程图中的一个节点（任务/Agent）
    """
    def __init__(
        self, 
        node_id: str, 
        label: str, 
        agent_type: AgentType, 
        detail: str = "", 
        device_id: str = "Server",
        status: str = "pending"
    ):
        """
        初始化任务节点
        
        Args:
            node_id (str): 唯一标识符，例如 "agent_research_a"
            label (str): 显示名称，例如 "信息搜集 A"
            agent_type (AgentType): Agent 类型 (planner, gui_agent, code_agent)
            detail (str): 任务详情描述，例如 "搜索关于 Topic X 的最新资料"
            device_id (str): 运行设备 ID，例如 "Desktop-1"
            status (str): 状态 (pending, running, completed, failed)
        """
        self.id = node_id
        self.label = label
        self.type = agent_type.value
        self.detail = detail
        self.device_id = device_id
        self.status = status
        # 可选：添加开始/结束时间
        self.start_time = None
        self.end_time = None

    def to_dict(self) -> Dict:
        data = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "detail": self.detail,
            "status": self.status
        }
        if self.type != "planner":
            data["device_id"] = self.device_id
        if self.start_time:
            data["start_time"] = self.start_time
        if self.end_time:
            data["end_time"] = self.end_time
        return data

class TaskFlowBuilder:
    """
    用于构建并行任务执行流程图的辅助工具类
    """
    def __init__(self, task_id: str, instruction: str):
        self.task_id = task_id
        self.instruction = instruction
        self.nodes: List[TaskNode] = []
        self.edges: List[Dict] = []

    def add_node(
        self, 
        node_id: str, 
        label: str, 
        agent_type: AgentType, 
        detail: str = "", 
        device_id: str = "Server"
    ) -> 'TaskFlowBuilder':
        """
        添加一个任务节点
        """
        node = TaskNode(node_id, label, agent_type, detail, device_id)
        self.nodes.append(node)
        return self

    def add_edge(
        self, 
        source_id: str, 
        target_id: str, 
        edge_type: EdgeType = EdgeType.CONTROL, 
        label: str = ""
    ) -> 'TaskFlowBuilder':
        """
        添加连接线（依赖关系）
        
        Args:
            source_id (str): 起始节点 ID
            target_id (str): 目标节点 ID
            edge_type (EdgeType): 边类型 (control 或 data_flow)
            label (str): 边上的标签（例如传递的文件名）
        """
        edge = {
            "source": source_id,
            "target": target_id,
            "type": edge_type.value
        }
        if label:
            edge["label"] = label
        self.edges.append(edge)
        return self

    def export_json(self, file_path: str):
        """
        导出为 JSON 文件
        """
        output = {
            "task_id": self.task_id,
            "instruction": self.instruction,
            "status": "created",
            "flow_graph": {
                "nodes": [node.to_dict() for node in self.nodes],
                "edges": self.edges
            }
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 流程图已导出至: {file_path}")

# ==========================================
# 使用示例 / 模板区域
# ==========================================

def create_my_parallel_task_flow():
    """
    在这里自定义你的任务流程
    """
    # 1. 初始化构建器
    builder = TaskFlowBuilder(
        task_id="task_custom_001", 
        instruction="示例：多Agent并行文档翻译与校对"
    )

    # 2. 添加节点 (Nodes)
    # -------------------------------------------------
    # 规划者
    builder.add_node(
        node_id="coordinator",
        label="任务分发",
        agent_type=AgentType.PLANNER,
        detail="将长文档拆分为 Part A 和 Part B，分别分发给翻译 Agent"
    )

    # 并行任务 A
    builder.add_node(
        node_id="translator_a",
        label="翻译 Agent A",
        agent_type=AgentType.GUI_AGENT,
        device_id="Docker-1",
        detail="使用 Google Translate 翻译文档的前半部分"
    )

    # 并行任务 B
    builder.add_node(
        node_id="translator_b",
        label="翻译 Agent B",
        agent_type=AgentType.GUI_AGENT,
        device_id="Docker-2",
        detail="使用 DeepL 翻译文档的后半部分"
    )

    # 汇聚任务
    builder.add_node(
        node_id="merger",
        label="合并与排版",
        agent_type=AgentType.CODE_AGENT,
        device_id="Docker-1", # 复用 Docker-1
        detail="使用 Python 脚本合并两部分文本，并生成最终 Word 文档"
    )

    # 3. 添加连线 (Edges)
    # -------------------------------------------------
    # 分发任务
    builder.add_edge("coordinator", "translator_a", EdgeType.CONTROL, label="Task: Part A")
    builder.add_edge("coordinator", "translator_b", EdgeType.CONTROL, label="Task: Part B")

    # 结果汇聚 (数据流)
    builder.add_edge("translator_a", "merger", EdgeType.DATA_FLOW, label="translated_part_a.txt")
    builder.add_edge("translator_b", "merger", EdgeType.DATA_FLOW, label="translated_part_b.txt")

    # 4. 导出
    builder.export_json("dataviewer/my_custom_flow.json")

if __name__ == "__main__":
    create_my_parallel_task_flow()

