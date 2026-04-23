"""
Agent Tool Registry
管理所有 Agent Tools 的注册和调用
"""
from typing import Dict, Optional, List
from desktop_env.controllers.python import PythonController
from .tool_definitions import get_agent_tools_definitions

# 开源版按需加载各个 GUI Agent tool。Claude 的 agent 依赖 benchmarkClient
# cookbook（未随开源版分发），所以用 try/except 给一个清晰的错误提示；
# 其余本地可运行的 agent 直接 import。
try:
    from .claude_gui_agent_as_tool import ClaudeGUIAgentTool
except ImportError as _exc:
    ClaudeGUIAgentTool = None
    _CLAUDE_IMPORT_ERR = _exc
else:
    _CLAUDE_IMPORT_ERR = None

from .kimi_gui_agent_as_tool import KimiGUIAgentTool
from .gpt_gui_agent_as_tool import GPTGUIAgentTool
from .qwen_gui_agent_as_tool import QwenGUIAgentTool
from .doubao_gui_agent_as_tool import DoubaoGUIAgentTool
from .seed18_gui_agent_as_tool import Seed18GUIAgentTool
from .gpt54_gui_agent_as_tool import GPT54GUIAgentTool


class AgentToolRegistry:
    """
    Agent Tools 注册表
    统一管理所有 Agent Tools,提供统一的调用接口
    """
    
    def __init__(
        self,
        controller: PythonController,
        use_gpt_gui: bool = True,
        use_qwen_gui: bool = False,
        use_doubao_gui: bool = False,
        use_kimi_gui: bool = False,
        use_seed18_gui: bool = False,
        use_gpt54_gui: bool = False,
        use_claude_gui: bool = False,
        vm_controllers: Optional[List[PythonController]] = None,
        gpt54_use_response_id: bool = True,
        gpt54_max_images: Optional[int] = None,
    ):
        """
        初始化注册表

        Args:
            controller: 共享的 PythonController 实例 (VM1)
            use_gpt_gui: 是否使用 GPT GUI Agent (默认True)
            use_qwen_gui: 是否使用 Qwen GUI Agent (默认False)
            use_doubao_gui: 是否使用 Doubao GUI Agent (默认False)
            use_kimi_gui: 是否使用 Kimi GUI Agent (默认False)
            use_seed18_gui: 是否使用 Seed 1.8 GUI Agent (默认False)
            use_gpt54_gui: 是否使用 GPT-5.4 GUI Agent (默认False)
            use_claude_gui: 是否显式使用 Claude Computer Use GUI Agent (默认False)
            vm_controllers: 多个VM控制器列表 [VM1, VM2, ...] (可选)
            gpt54_use_response_id: GPT-5.4 是否使用 previous_response_id 有状态模式 (默认True)
            gpt54_max_images: GPT-5.4 保留的历史截图数量 (None=全部，N=最近N张)
        """
        self.controller = controller
        self.vm_controllers = vm_controllers or [controller]
        self.use_gpt_gui = use_gpt_gui  # 保存设置以便后续使用
        self.use_qwen_gui = use_qwen_gui  # 保存Qwen设置
        self.use_doubao_gui = use_doubao_gui  # 保存Doubao设置
        self.use_kimi_gui = use_kimi_gui  # 保存Kimi设置
        self.use_seed18_gui = use_seed18_gui  # 保存Seed18设置
        self.use_gpt54_gui = use_gpt54_gui  # 保存GPT54设置
        self.use_claude_gui = use_claude_gui  # 保存Claude设置（显式选择）
        self.gpt54_use_response_id = gpt54_use_response_id  # GPT-5.4 有状态模式
        self.gpt54_max_images = gpt54_max_images  # GPT-5.4 历史截图数量

        # 调试输出
        print(
            "[AgentToolRegistry DEBUG] "
            f"use_gpt_gui={use_gpt_gui}, "
            f"use_qwen_gui={use_qwen_gui}, "
            f"use_doubao_gui={use_doubao_gui}, "
            f"use_kimi_gui={use_kimi_gui}, "
            f"use_seed18_gui={use_seed18_gui}, "
            f"use_gpt54_gui={use_gpt54_gui}, "
            f"use_claude_gui={use_claude_gui}"
        )
        
        # 初始化所有 GUI Agent Tools (VM1)
        self.claude_gui_agent_tool = ClaudeGUIAgentTool(controller)
        self.gpt_gui_agent_tool = GPTGUIAgentTool(controller)
        self.qwen_gui_agent_tool = QwenGUIAgentTool(controller)
        self.kimi_gui_agent_tool = None
        
        # 尝试初始化 Doubao GUI Agent，捕获可能的错误
        try:
            self.doubao_gui_agent_tool = DoubaoGUIAgentTool(controller)
            print(f"[AgentToolRegistry DEBUG] DoubaoGUIAgentTool initialized successfully")
        except Exception as e:
            print(f"[AgentToolRegistry ERROR] Failed to initialize DoubaoGUIAgentTool: {e}")
            import traceback
            traceback.print_exc()
            self.doubao_gui_agent_tool = None

        try:
            self.kimi_gui_agent_tool = KimiGUIAgentTool(controller)
            print(f"[AgentToolRegistry DEBUG] KimiGUIAgentTool initialized successfully")
        except Exception as e:
            print(f"[AgentToolRegistry ERROR] Failed to initialize KimiGUIAgentTool: {e}")
            import traceback
            traceback.print_exc()
            self.kimi_gui_agent_tool = None
        
        # 尝试初始化 Seed 1.8 GUI Agent（三层 fallback 解析，不依赖 ui_tars）
        try:
            self.seed18_gui_agent_tool = Seed18GUIAgentTool(controller)
            print(f"[AgentToolRegistry DEBUG] Seed18GUIAgentTool initialized successfully")
        except Exception as e:
            print(f"[AgentToolRegistry ERROR] Failed to initialize Seed18GUIAgentTool: {e}")
            import traceback
            traceback.print_exc()
            self.seed18_gui_agent_tool = None

        # 尝试初始化 GPT-5.4 GUI Agent（OpenAI Responses API + computer-use）
        try:
            self.gpt54_gui_agent_tool = GPT54GUIAgentTool(
                controller,
                use_response_id=self.gpt54_use_response_id,
                max_images=self.gpt54_max_images,
            )
            print(f"[AgentToolRegistry DEBUG] GPT54GUIAgentTool initialized successfully"
                  f" (use_response_id={self.gpt54_use_response_id}, max_images={self.gpt54_max_images})")
        except Exception as e:
            print(f"[AgentToolRegistry ERROR] Failed to initialize GPT54GUIAgentTool: {e}")
            import traceback
            traceback.print_exc()
            self.gpt54_gui_agent_tool = None

        # 选择使用哪个 GUI Agent 实现
        # 优先级: Claude(显式) > GPT54 > Seed18 > Kimi > Doubao > Qwen > GPT > Claude(fallback)
        if use_claude_gui:
            active_gui_agent = self.claude_gui_agent_tool
            print(f"[AgentToolRegistry] Using Claude Computer Use GUI Agent (显式选择)")
        elif use_gpt54_gui:
            active_gui_agent = self.gpt54_gui_agent_tool
            print(f"[AgentToolRegistry] Using GPT-5.4 GUI Agent (gpt-5.4-mini, Responses API)")
        elif use_seed18_gui:
            active_gui_agent = self.seed18_gui_agent_tool
            print(f"[AgentToolRegistry] Using Seed 1.8 GUI Agent (doubao-seed-1-8-251228, 3-layer fallback)")
        elif use_kimi_gui:
            active_gui_agent = self.kimi_gui_agent_tool
            print(f"[AgentToolRegistry] Using Kimi GUI Agent (kimi-k2.5)")
        elif use_doubao_gui:
            active_gui_agent = self.doubao_gui_agent_tool
            print(f"[AgentToolRegistry] Using Doubao GUI Agent (doubao-seed-1-8-251228)")
        elif use_qwen_gui:
            active_gui_agent = self.qwen_gui_agent_tool
            print(f"[AgentToolRegistry] Using Qwen3 GUI Agent (qwen3-vl)")
        elif use_gpt_gui:
            active_gui_agent = self.gpt_gui_agent_tool
            print(f"[AgentToolRegistry] Using GPT GUI Agent")
        else:
            active_gui_agent = self.claude_gui_agent_tool
            print(f"[AgentToolRegistry] Using Claude GUI Agent")
        
        # 工具名称到实例的映射
        self._tools = {
            "gui_agent": active_gui_agent,  # gui_agent 名称指向活跃实现
            "claude_gui_agent": self.claude_gui_agent_tool,
            "gpt_gui_agent": self.gpt_gui_agent_tool,
            "qwen_gui_agent": self.qwen_gui_agent_tool,
            "doubao_gui_agent": self.doubao_gui_agent_tool,
            "kimi_gui_agent": self.kimi_gui_agent_tool,
            "seed18_gui_agent": self.seed18_gui_agent_tool,
            "gpt54_gui_agent": self.gpt54_gui_agent_tool,
        }
    
    def execute(
        self, 
        tool_name: str, 
        task: str, 
        max_rounds: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Dict:
        """
        执行指定的 Agent Tool
        
        Args:
            tool_name: 工具名称 ("gui_agent", "claude_gui_agent", "gpt_gui_agent", "qwen_gui_agent", "doubao_gui_agent", "kimi_gui_agent", "seed18_gui_agent", "gpt54_gui_agent")
                      注意: "gui_agent" 指向当前活跃的GUI Agent实现(Seed18/Kimi/Doubao/Qwen/GPT/Claude)
            task: 任务描述
            max_rounds: 最大执行轮次 (可选,使用默认值)
            timeout: 超时时间 (可选,使用默认值)
        
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
        kwargs: Dict = {"task": task}
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
    
    def get_available_tools(self):
        """获取所有可用的工具列表"""
        return list(self._tools.keys())
    
    def get_tools_definitions(self):
        """获取所有工具的 MCP 定义"""
        return get_agent_tools_definitions()
