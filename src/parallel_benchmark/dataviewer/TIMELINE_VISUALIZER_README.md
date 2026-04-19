# 时间轴可视化工具使用说明

## 概述

`timeline_visualizer.html` 是一个基于 Plotly.js 的交互式可视化工具，用于展示多智能体执行记录的任务分解和执行时间线。

## 多智能体系统架构

这是一个面向多 Docker 的多智能体系统：

```
┌─────────────────────────────────────────────────────────────┐
│                      Plan Agent                              │
│  - 读取用户指令，分解为可并行的子任务                           │
│  - 在每个 Round 中调用 gui_agent/code_agent 执行任务           │
│  - 等待 Agent 返回结果后进入下一个 Round                       │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │  Docker 1   │    │  Docker 2   │    │  Docker N   │
    │ gui_agent_1 │    │ gui_agent_2 │    │ gui_agent_N │
    │ code_agent  │    │ code_agent  │    │ code_agent  │
    └─────────────┘    └─────────────┘    └─────────────┘
```

**Agent 调用层级**：
- **Plan Agent**：顶层协调者，负责任务分解和调度
- **GUI Agent**：每个 Docker 上一个，通过模拟人类操作（点击、输入等）完成前台任务
- **Code Agent**：每个 Docker 上可有多个，通过执行代码完成后台任务

**Round 概念**：
- Plan Agent 的每个 Round 代表一次"思考 + 调度"周期
- GUI/Code Agent 的每个 Round 代表一次"思考 + 执行动作"周期
- GUI Agent 的一个 Round 内可能包含多轮对话（多次截图 + 多次动作）

## 可视化展示逻辑

### Level 1: 全局执行视图（甘特图）

```
纵轴                横轴（时间）
────────────────────────────────────────────────────
Plan Agent    ████████████████████████████████████
              ↑ Round 0      ↑ Round 1
              
gui_agent_1   ████████████████████    ████████
              ↑ 第一次调用     ────→   ↑ 第二次调用（依赖线）
              
gui_agent_2   ████████████████████
              ↑ 只调用一次
────────────────────────────────────────────────────
```

**纵轴排列规则**：
- 第一行：Plan Agent
- 后续行：按 `agent_id` 名称排序（gui_agent_1, gui_agent_2, code_agent_1...）
- 同一个 `agent_id` 的多次调用在**同一行**显示多段时间条

**依赖关系线**：
- 如果 Plan Round N 中的 agent 调用依赖于 Plan Round M 中的结果
- 画一条直线从 Round M 的结束时间连到 Round N 的开始时间
- 示例：`gui_agent_1` 在 Round 1 依赖 Round 0 的结果 → 从 Round 0 结束画线到 Round 1 开始

### Level 2: Agent Round 详情视图

点击 Level 1 中的 Agent 时间条，进入该 Agent 的 Round 详情视图：

```
纵轴                横轴（时间）
────────────────────────────────────────────────────
Plan Agent    ████████████████████████████████████

gui_agent_2   ██ ██ ██ ██ ██ ██ ██ ██ ██ ██
(Rounds详情)  R0 R1 R2 R3 R4 R5 R6 R7 R8 R9
              ↑ 每个绿色块是一个 Round（Model Thinking）
────────────────────────────────────────────────────
```

- 绿色块：Model Thinking 时间（模型思考）
- 红色块：Action Execution 时间（执行动作）
- 点击某个 Round 块查看详细信息

### Level 3: Round 详细信息面板

点击 Level 2 中的 Round 块，显示详细信息面板：

```
┌────────────────────────────────────────────────────┐
│ gui_agent_2 - Round 1                    [GUI Agent]│
├────────────────────────────────────────────────────┤
│ [Round 0] [Round 1] [Round 2] ... (Round 选择器)    │
├────────────────────────────────────────────────────┤
│ 时间统计                                            │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│ │ 6.161s   │ │ 1.694s   │ │ 6.286s   │ │  失败   │ │
│ │Model Time│ │Action Time│ │Total Time│ │执行状态 │ │
│ └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
├────────────────────────────────────────────────────┤
│ 执行截图 (3 张)                                     │
│ ┌─────┐ ┌─────┐ ┌─────┐                           │
│ │     │ │     │ │     │                           │
│ │Step1│ │Step4│ │Step7│  ← 同一 Round 内的多轮对话 │
│ └─────┘ └─────┘ └─────┘                           │
├────────────────────────────────────────────────────┤
│ 动作序列                                            │
│ [1] gui_action: left_click                         │
├────────────────────────────────────────────────────┤
│ Model Response / 执行结果 / 执行代码                │
└────────────────────────────────────────────────────┘
```

**截图 Step 编号说明**：
- GUI Agent 的一个 Round 可能包含多轮对话（Model ↔ Tool 交互）
- 每轮对话的 user 消息中包含当前截图
- Step 编号 = messages 数组中的索引 + 1
- 例如：Step 1, 4, 7 表示 messages[0], messages[3], messages[6] 中的截图
- 这是**正常现象**，表示该 Round 内进行了多次交互

## 快速访问面板

页面底部提供快速访问按钮：
- `Plan Round 0/1/...`：查看 Plan Agent 的某个 Round 详情
- `gui_agent_1/2/...`：跳转到该 Agent 的 Round 详情视图，并自动显示第一个 Round 的详细信息

## 使用方法

### 1. 准备数据

```bash
# 如果 JSON 中包含 base64 图片，先提取到文件
python extract_images.py execution_record_xxx.json
# 这会将图片保存到 extracted_images/ 目录
```

### 2. 启动本地服务器（推荐）

```bash
cd dataviewer
python -m http.server 8000
# 浏览器访问 http://localhost:8000/timeline_visualizer.html
```

### 3. 加载数据

1. 点击"📁 选择 JSON 文件"或"📊 加载示例数据"
2. 自动显示 Level 1 全局执行视图
3. 点击时间条/快速访问按钮查看详情

## 数据格式要求

JSON 文件需要符合以下结构：

```json
{
  "metadata": {
    "start_timestamp": 1766478885.88,
    "duration": 195.67
  },
  "plan_agent": {
    "rounds": [
      {
        "round_id": 0,
        "model_prediction": { "time_span": {...} },
        "action_execution": { "time_span": {...} },
        "dependencies": {
          "gui_agent_1": {
            "round": 1,
            "depends_on": [{ "agent_id": "gui_agent_1", "round": 0 }]
          }
        }
      }
    ]
  },
  "devices": [
    {
      "device_id": "Desktop-0",
      "agents": [
        {
          "agent_id": "gui_agent_1",
          "type": "gui",
          "parent_round": 0,
          "rounds": [
            {
              "round_id": 0,
              "model_prediction": {
                "time_span": {...},
                "messages": [
                  { "role": "user", "content": [{ "type": "image_url", "image_url": {...} }] }
                ]
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## 故障排除

### 图片无法显示
- 确保使用 `python -m http.server` 方式访问（而非 file://）
- 确保 `extracted_images/` 目录与 HTML 文件同级
- 检查图片路径是否以 `extracted_images/` 开头

### 依赖线位置不对
- 检查 `dependencies` 中的 `round` 字段是否正确（1-indexed）
- 检查 Agent 的 `parent_round` 字段是否正确

### Agent 显示多行
- 同一个 `agent_id` 应该只显示一行，多次调用在同一行显示多段时间条
- 检查数据中 `agent_id` 是否一致

## 技术实现

### 主要函数
- `renderLevel1()` - 渲染全局执行视图
- `renderAgentRoundsLevel()` - 渲染 Agent Round 详情视图
- `showAgentRoundDetail()` - 显示 Round 详细信息面板
- `collectRoundImages()` - 收集 Round 中的所有截图
- `addConnectionLines()` - 绘制依赖关系线

### 颜色配置
```javascript
const COLORS = {
    plannerThink: '#a371f7',   // Plan Agent 思考阶段（紫色）
    plannerAction: '#c9b1fb',  // Plan Agent 执行阶段（浅紫色）
    gui: '#58a6ff',            // GUI Agent（蓝色）
    code: '#d29922',           // Code Agent（橙色）
    modelTime: '#3fb950',      // Model Thinking（绿色）
    actionTime: '#f85149',     // Action Execution（红色）
    dependency: '#f97583'      // 依赖关系线（粉红色）
};
```

## 更新日志

### 2025-12-25: 交互优化 - Toggle 展开/折叠功能

**修改内容**：

1. **`navigateToAgent` 函数支持 Toggle**
   - **之前**：点击快速访问面板中的 Agent 按钮只能展开该实例，无法折叠恢复
   - **之后**：支持 Toggle 切换 - 点击已展开的实例可以折叠恢复原状

2. **修改代码**：
   ```javascript
   // 导航到特定 Agent（在 Level 1 中展开/折叠该实例，支持 toggle）
   function navigateToAgent(agentLabel) {
       recordData.devices.forEach(device => {
           device.agents.forEach(agent => {
               const key = getAgentKey(agent);
               if (key === agentLabel || agent.agent_count === agentLabel) {
                   const callKey = getAgentKey(agent);
                   
                   // Toggle: 如果已展开则折叠，否则展开
                   if (expandedAgents.has(callKey)) {
                       expandedAgents.delete(callKey);
                   } else {
                       expandedAgents.add(callKey);
                   }
                   
                   renderLevel1();
                   
                   // 如果展开了，显示第一个 Round 的详情面板
                   if (expandedAgents.has(callKey) && agent.rounds?.length > 0) {
                       showAgentRoundDetail(agent, 0);
                   }
               }
           });
       });
   }
   ```

3. **交互行为说明**：
   - **点击图表时间条**：Toggle 展开/折叠该 Agent 实例
   - **点击快速访问按钮**：Toggle 展开/折叠该 Agent 实例（与时间条点击行为一致）
   - **展开状态**：显示每个 Round 的思考（绿色）和执行（红色）分段
   - **折叠状态**：显示整体时间条（GUI Agent 蓝色，Code Agent 橙色）

4. **实例级别控制**：
   - 使用 `agent_count`（callKey）而非 `agent_id`（groupKey）追踪展开状态
   - 同一 `agent_id` 的多次调用可以独立展开/折叠
   - 例如：`gui_agent_1` 被调用两次，可以单独展开第一次调用而保持第二次调用折叠

## 参考

- 数据结构定义：`record_template.py`
- Agent ID 规范：`agent_id格式说明.md`
- 设计文档：`visualization_design.md`
