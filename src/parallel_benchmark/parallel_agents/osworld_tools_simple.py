# OSWorld OS 工具定义 - 添加到 tool_agent.py 的 LOCAL_TOOLS 列表中

OSWORLD_OS_TOOLS = [
    # 音量控制
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current system volume percentage (0-100)",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "category": "osworld_os"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the system volume to a specific percentage (0-100)",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "description": "Volume percentage (0-100)"}
                },
                "required": ["percent"]
            },
            "category": "osworld_os"
        }
    },
    # 文件搜索
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by keyword in their basename (case-insensitive)",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword to search"},
                    "root_dir": {"type": "string", "description": "Root directory (default: /home/user/Desktop)", "default": "/home/user/Desktop"}
                },
                "required": ["keyword"]
            },
            "category": "osworld_os"
        }
    },
    # 计算器
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression. Supports +, -, *, /, **, %, //",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression like '(3+5)*2'"}
                },
                "required": ["expression"]
            },
            "category": "osworld_os"
        }
    },
    # Git 操作
    {
        "type": "function",
        "function": {
            "name": "git_set_user",
            "description": "Set git username and email globally",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Git username"},
                    "email": {"type": "string", "description": "Git email"}
                },
                "required": ["username", "email"]
            },
            "category": "osworld_os"
        }
    },
]
