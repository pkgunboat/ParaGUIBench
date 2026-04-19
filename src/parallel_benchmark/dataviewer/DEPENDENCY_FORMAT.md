# 依赖关系格式说明

## ✅ 新格式（使用 unique_id）

```json
{
  "round": 2,
  "thought": "Based on the data from previous round...",
  "dependencies": {
    "call_gui_agent_1": {
      "round": 2,
      "task": "Read and display the data file",
      "depends_on": [
        {
          "agent_id": "call_code_agent_1",
          "round": 1,
          "reason": "Uses same files"
        }
      ]
    },
    "call_gui_agent_2": {
      "round": 2,
      "task": "Search for weather information",
      "depends_on": []
    }
  },
  "tool_calls": [...],
  "results": [...]
}
```

### 格式说明

**外层结构** - 字典，键是 agent 的 unique_id：
- **unique_id**: 格式为 `call_{function_name}_{counter}`
  - 例如: `call_gui_agent_1`, `call_code_agent_2`, `call_gui_agent_vm2_1`

**每个 agent 的信息** - 包含三个字段：
- **round** (int): 该 agent 在哪一轮被调用
- **task** (string): 任务描述（最多100字符）
- **depends_on** (array): 依赖列表，每项包含：
  - **agent_id** (string): 依赖的 agent 的 unique_id（格式: `call_{function_name}_{counter}`）
  - **round** (int): 被依赖的 agent 在哪一轮
  - **reason** (string): 依赖原因说明

### 示例解读

```json
{
  "call_gui_agent_1": {
    "round": 2,
    "task": "Read and combine data from previous agents",
    "depends_on": [
      {
        "agent_id": "call_code_agent_1",
        "round": 1,
        "reason": "Uses same files"
      },
      {
        "agent_id": "call_gui_agent_vm2_1",
        "round": 1,
        "reason": "References GUI agent data"
      }
    ]
  }
}
```

- Agent `call_gui_agent_1` 在 Round 2 被调用
- 它依赖于两个 Round 1 的 agents
- 依赖原因分别是：使用相同文件、引用 GUI 结果

## 控制台输出示例

```
[DEPENDENCY ANALYSIS] Analyzing dependencies...
  📍 call_gui_agent_2 (Read and combine data...)
      ⬅️  depends on: call_code_agent_1, call_gui_agent_1
  🆕 call_code_agent_2 (Search for weather information...) - No dependencies
```

## 可视化工具

使用 `show_dependencies.py` 查看依赖关系：

```bash
python ubuntu_env/parallel_benchmark/dataviewer/show_dependencies.py logs/execution_record.json
```

输出示例：
```
================================================================================
ROUND 2
================================================================================
💭 Thought: I need to combine the data from previous round...

  Agents in this round: 2

  🔧 call_gui_agent_2
     Round: 2 | Task: Read and combine data from previous agents...
     Dependencies:
       ⬅️  call_code_agent_1 (Round 1)
           Reason: Uses same files
       ⬅️  call_gui_agent_vm2_1 (Round 1)
           Reason: References GUI agent data

  🔧 call_code_agent_2
     Round: 2 | Task: Search for weather information...
     🆕 No dependencies (independent task)
```

## 优势

✅ **使用 unique_id**: 格式为 `call_{function_name}_{counter}`，清晰易读  
✅ **保留关键信息**: round、task、reason 帮助理解依赖关系  
✅ **结构清晰**: 字典格式便于编程访问  
✅ **易于调试**: 可以通过 unique_id 快速定位具体的 agent 执行记录  

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| unique_id (key) | string | Agent 的唯一标识符（格式: `call_{function_name}_{counter}`） |
| round | int | 该 agent 在哪一轮被调用 |
| task | string | 任务描述（截取前100字符） |
| depends_on | array | 依赖的 agent 列表 |
| depends_on[].agent_id | string | 被依赖的 agent 的 unique_id（格式: `call_{function_name}_{counter}`） |
| depends_on[].round | int | 被依赖的 agent 所在轮次 |
| depends_on[].reason | string | 依赖原因（自动检测） |

## 依赖检测策略

系统会自动检测以下几种依赖关系：

1. **明确提及轮次**: 在 thought 或 task 中提到 "Round 1"、"第一轮" 等
2. **工具类型引用**: 提到 "GUI agent data"、"code agent result" 等
3. **文件路径共享**: 当前任务和之前任务使用相同的文件路径
4. **关键词检测**: "previous", "之前", "based on", "using the" 等依赖关键词

## 兼容性

可视化工具 `show_dependencies.py` 支持多种格式，会自动检测并适配：
- 新格式（unique_id + round/task/reason）
- 中间格式（unique_id + 依赖列表）
- 旧格式（tool_call_id + 完整依赖对象）
