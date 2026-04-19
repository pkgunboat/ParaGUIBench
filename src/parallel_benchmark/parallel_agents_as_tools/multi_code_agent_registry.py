"""
Multi Code Agent Registry
管理多个并行的 Code Agent，都在同一个 VM 上运行
"""
from typing import Dict, Optional, List
from desktop_env.controllers.python import PythonController
from .code_agent_as_tool import CodeAgentTool


class MultiCodeAgentRegistry:
    """
    多 Code Agent 注册表
    支持在同一个 VM 上运行多个 Code Agent 实例
    """
    
    def __init__(
        self, 
        controller: PythonController, 
        max_code_agents: int = 5
    ):
        """
        初始化注册表
        
        Args:
            controller: PythonController 实例 (所有 Code Agent 共享)
            max_code_agents: 最大 Code Agent 数量
        """
        self.controller = controller
        self.max_code_agents = max_code_agents
        
        # 创建多个 Code Agent 实例（共享同一个 controller）
        self._code_agents: Dict[str, CodeAgentTool] = {}
        for i in range(1, max_code_agents + 1):
            agent_name = f"code_agent_{i}"
            self._code_agents[agent_name] = CodeAgentTool(controller)
        
        # 工具名称到实例的映射
        self._tools = dict(self._code_agents)
    
    def execute(
        self, 
        tool_name: str, 
        task: str, 
        max_rounds: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Dict:
        """
        执行指定的 Code Agent
        
        Args:
            tool_name: 工具名称 ("code_agent_1", "code_agent_2", ...)
            task: 任务描述
            max_rounds: 最大执行轮次
            timeout: 超时时间
        
        Returns:
            执行结果字典
        """
        # 获取工具实例
        tool = self._tools.get(tool_name)
        
        if not tool:
            return {
                "status": "failure",
                "result": "",
                "steps": [],
                "error": f"Unknown tool: {tool_name}. Available tools: {list(self._tools.keys())}"
            }
        
        # 准备参数
        kwargs = {"task": task}
        if max_rounds is not None:
            kwargs["max_rounds"] = max_rounds
        if timeout is not None:
            kwargs["timeout"] = timeout
        
        # 执行工具
        try:
            result = tool.execute(**kwargs)
            return result
        except Exception as e:
            return {
                "status": "failure",
                "result": "",
                "steps": [],
                "error": f"Unexpected error executing {tool_name}: {str(e)}"
            }
    
    def get_available_tools(self) -> List[str]:
        """获取所有可用的工具列表"""
        return list(self._tools.keys())
    
    def get_tools_definitions(self) -> List[Dict]:
        """
        获取所有工具的定义（用于 OpenAI function calling）
        """
        definitions = []
        for i in range(1, self.max_code_agents + 1):
            definitions.append({
                "type": "function",
                "function": {
                    "name": f"call_code_agent_{i}",
                    "description": f"""Execute programmatic tasks using Code Agent #{i}: file operations, system commands, calculations, data processing, text translation/summarization. All agents share the same VM filesystem.

IMPORTANT: When running in parallel with other Code Agents, each agent MUST:
1. Write results to a UNIQUE output file: /home/user/shared/agent_{i}_result.json
2. The JSON file should contain: {{"status": "success/failure", "result": "...", "error": "..."}}
3. Do NOT rely on print() for returning results - write to the file instead!

This ensures parallel execution results don't get mixed up.""",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_description": {
                                "type": "string",
                                "description": f"Clear description of what to do. MUST include: 'Write your result to /home/user/shared/agent_{i}_result.json'"
                            }
                        },
                        "required": ["task_description"]
                    }
                }
            })
        return definitions
