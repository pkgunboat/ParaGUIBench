# ToolAgent 工具添加指南

## 📋 概述

本指南介绍如何从 OSWorld-MCP 或其他来源添加新工具到 ToolAgent 中。

## 🎯 三步集成法

### 步骤 1️⃣：分析工具代码

**从源文件中找到工具实现**

例如从 `os.py` 或 `google_chrome.py` 中复制工具函数：

```python
@staticmethod
def calculator(expression: str) -> str:
    # 工具实现逻辑
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        # ...
    }
    return result
```

**关键点：**
- ✅ 优先选择**同步函数**（无 async/await）
- ✅ 依赖尽量只用 Python 标准库
- ⚠️ 如需特殊库（pyautogui、playwright），确保 VM 中已安装

---

### 步骤 2️⃣：定义工具（Tool Schema）

**文件：** `parallel_agents/tool_agent.py`

**位置：** `LOCAL_TOOLS` 列表末尾

**格式：**
```python
{
    "type": "function",
    "function": {
        "name": "工具名称",  # 蛇形命名：get_volume, search_files
        "description": "清晰的功能描述，GPT 根据这个判断是否使用该工具",
        "parameters": {
            "type": "object",
            "properties": {
                "参数名": {
                    "type": "string/integer/boolean",
                    "description": "参数说明"
                }
            },
            "required": ["必填参数"]
        },
        "category": "分类标签"  # system/file/browser/utility/development
    }
}
```

**示例：**
```python
{
    "type": "function",
    "function": {
        "name": "chrome_bookmark_page",
        "description": "Bookmark the current Chrome page using Ctrl+D.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "category": "browser"
    }
}
```

---

### 步骤 3️⃣：实现执行逻辑

**文件：** `parallel_agents_as_tools/tool_agent_as_tool.py`

**位置：** `_execute_tool()` 方法中，`else` 语句之前

**格式：**
```python
elif tool_name == "工具名称":  # 与步骤2的name保持一致
    # 1. 提取参数
    param1 = arguments.get("param1", "默认值")
    param2 = arguments.get("param2", 0)
    
    # 2. 构建要执行的 Python 代码
    code = f"""
import 需要的库

def 工具函数名(参数):
    # 从步骤1复制的工具逻辑
    # 直接嵌入实现代码
    try:
        result = do_something()
        return f"Success: {{result}}"
    except Exception as e:
        return f"Error: {{e}}"

# 调用函数并打印结果
param1 = '{param1}'
param2 = {param2}
result = 工具函数名(param1, param2)
print(result)
"""
```

**示例：**
```python
elif tool_name == "chrome_bookmark_page":
    code = """
import pyautogui
import time

def chrome_bookmark_page():
    try:
        pyautogui.hotkey('ctrl', 'd')
        time.sleep(0.5)
        return "Bookmarked current Chrome page"
    except Exception as e:
        return f"Error: {{e}}"

result = chrome_bookmark_page()
print(result)
"""
```

---

## 📊 当前工具统计总览

**总计：14 个工具** 

---

## 🗂️ 工具详细列表

### 📁 本地工具管理（Local Tools）- 2 个

| # | 工具名称 | 功能描述 | 工具种类 | 实现方式 | 操作类型 | 状态 |
|---|---------|---------|---------|---------|---------|------|
| 1 | `list_local_tools` | 列出虚拟机 `/home/user/Desktop/tools/` 目录中的所有 Python 工具脚本 | 本地工具 | os.listdir + 文件过滤 | 后台 | ✅ |
| 2 | `execute_local_tool` | 执行 tools 目录中的指定 Python 脚本，支持传递参数 | 本地工具 | subprocess.run | 后台 | ✅ |

---

### 🖥️ 系统操作（System Operations）- 5 个

| # | 工具名称 | 功能描述 | 工具种类 | 实现方式 | 操作类型 | 状态 |
|---|---------|---------|---------|---------|---------|------|
| 3 | `read_file` | 读取指定文件的内容并返回 | 文件操作 | open() + read() | 后台 | ✅ |
| 4 | `list_directory` | 列出目录中的所有文件和文件夹 | 文件操作 | os.listdir + os.path | 后台 | ✅ |
| 5 | `check_file_exists` | 检查文件或目录是否存在，返回类型信息 | 文件操作 | os.path.exists | 后台 | ✅ |
| 6 | `get_volume` | 获取当前系统音量（0-100%） | 系统设置 | pactl (PulseAudio) | 后台 | ⚠️ VM无音频 |
| 7 | `set_volume` | 设置系统音量到指定百分比（0-100%） | 系统设置 | pactl (PulseAudio) | 后台 | ⚠️ VM无音频 |

---

### 🔍 实用工具（Utilities）- 2 个

| # | 工具名称 | 功能描述 | 工具种类 | 实现方式 | 操作类型 | 状态 |
|---|---------|---------|---------|---------|---------|------|
| 8 | `calculator` | 安全计算数学表达式，支持 +, -, *, /, ** 运算 | 计算工具 | AST 解析 + operator | 后台 | ✅ |
| 9 | `search_files` | 在指定目录递归搜索包含关键词的文件（不区分大小写） | 文件搜索 | os.walk + 字符串匹配 | 后台 | ✅ |

---

### 👨‍💻 开发工具（Development）- 1 个

| # | 工具名称 | 功能描述 | 工具种类 | 实现方式 | 操作类型 | 状态 |
|---|---------|---------|---------|---------|---------|------|
| 10 | `git_set_user` | 设置全局 Git 用户名和邮箱配置 | Git 配置 | git config --global | 后台 | ✅ |

---

### 🌐 浏览器工具（Browser - Chrome）- 4 个

| # | 工具名称 | 功能描述 | 工具种类 | 实现方式 | 操作类型 | 状态 |
|---|---------|---------|---------|---------|---------|------|
| 11 | `chrome_restore_tab` | 恢复最后关闭的 Chrome 标签页 | 浏览器操作 | pyautogui + Ctrl+Shift+T | 前台 | ✅ |
| 12 | `chrome_print_page` | 打开 Chrome 的打印对话框 | 浏览器操作 | pyautogui + Ctrl+P | 前台 | ✅ |
| 13 | `chrome_bookmark_page` | 收藏当前 Chrome 页面 | 浏览器操作 | pyautogui + Ctrl+D | 前台 | ✅ |
| 14 | `chrome_clear_data` | 打开 Chrome 清除浏览数据对话框 | 浏览器操作 | pyautogui + Ctrl+Shift+Del | 前台 | ✅ |

---

## ✅ 集成检查清单

- [ ] 步骤1：从源文件复制工具代码
- [ ] 步骤2：在 `tool_agent.py` 的 `LOCAL_TOOLS` 添加定义
  - [ ] `name` 使用蛇形命名
  - [ ] `description` 清晰描述功能
  - [ ] `parameters` 定义所有参数
  - [ ] `required` 列出必填参数
- [ ] 步骤3：在 `tool_agent_as_tool.py` 添加 `elif` 分支
  - [ ] `tool_name` 与步骤2的 `name` 一致
  - [ ] 从 `arguments` 提取参数
  - [ ] 代码嵌入到 `code = f"""..."""`
  - [ ] 字符串正确转义
  - [ ] 最后 `print()` 输出结果
- [ ] 创建测试脚本验证功能
- [ ] 上传文件到服务器
- [ ] 运行测试确认工作正常

---


## 📝 维护建议

1. **命名规范**
   - 使用动词开头：`get_`, `set_`, `open_`, `search_`
   - 蛇形命名：`search_files` 而非 `searchFiles`
   - 名称要清晰：`chrome_bookmark_page` 而非 `bookmark`

2. **描述规范**
   - 描述清晰完整，包含关键功能点
   - 说明参数用途和格式
   - 提及快捷键或特殊要求

3. **错误处理**
   - 每个工具都应该有 try-except
   - 错误信息要清晰：`f"Error: {e}"`
   - 成功返回有意义的信息


---