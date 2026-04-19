"""
坐标系统验证结论
==================

问题: 不同模型使用不同坐标系统，标记是否能正确对应到1920x1080的真实坐标？

验证结果: ✅ 系统设计正确，标记坐标可靠
========================================

## 1. 坐标流转过程

### Claude 模型
```
步骤1: 截图
  - 获取1920x1080的原始截图
  - 如果 screenshot_compression=True 且 size > max_screenshot_size:
    * 缩放图片 (如 768x432)
    * 设置 scale_x = 1920/768 = 2.5
  - 否则: 不缩放，scale_x = 1.0

步骤2: 模型推理
  - 输入缩放后的图片 (或原图)
  - 模型返回基于输入图片的坐标
  - 如果图片是768x432，返回 [300, 200]
  - 如果图片是1920x1080，返回 [750, 500]

步骤3: 坐标转换 (execute_computer_action)
  - real_x = int(x * scale_x)
  - real_y = int(y * scale_y)
  - 如果缩放: [300, 200] → [750, 500]
  - 如果不缩放: [750, 500] → [750, 500]

步骤4: PyAutoGUI执行
  - 使用转换后的真实坐标 [750, 500]

步骤5: ExecutionRecorder记录
  - ⚠️ 记录API返回的原始messages
  - coordinate = 模型的原始输出
  - 如果缩放: [300, 200] (缩放图坐标)
  - 如果不缩放: [750, 500] (真实坐标)
```

### GPT 模型
```
步骤1: 截图
  - 获取1920x1080的原始截图
  - 发送给GPT

步骤2: 模型推理  
  - GPT使用1000x1000相对坐标系
  - 返回相对坐标，如 [500, 300]

步骤3: 坐标转换 (gpt_action_parser.py)
  - x_actual = int(x / 1000 * 1920) = 960
  - y_actual = int(y / 1000 * 1080) = 324
  - [500, 300] → [960, 324]

步骤4: PyAutoGUI执行
  - 使用转换后的真实坐标 [960, 324]

步骤5: ExecutionRecorder记录
  - ⚠️ 记录API返回的原始messages
  - coordinate = [500, 300] (GPT原始相对坐标)
```

### Qwen 模型
```
步骤1: 截图
  - 获取1920x1080的原始截图
  - 发送给Qwen

步骤2: 模型推理
  - Qwen使用1000x1000相对坐标系
  - 返回相对坐标，如 [500, 300]

步骤3: 坐标转换 (qwen_action_parser.py)
  - x_actual = int(x / 1000 * 1920) = 960
  - y_actual = int(y / 1000 * 1080) = 324
  - [500, 300] → [960, 324]

步骤4: PyAutoGUI执行
  - 使用转换后的真实坐标 [960, 324]

步骤5: ExecutionRecorder记录
  - ⚠️ 记录API返回的原始messages
  - coordinate = [500, 300] (Qwen原始相对坐标)
```

## 2. 当前问题

❌ **JSON中记录的是模型原始坐标，而不是转换后的坐标！**

这意味着：
- Claude (不缩放): JSON中是 [750, 500] ✅ 正确 (1920x1080坐标)
- Claude (缩放):   JSON中是 [300, 200] ❌ 错误 (768x432坐标)
- GPT:            JSON中是 [500, 300] ❌ 错误 (1000x1000相对坐标)
- Qwen:           JSON中是 [500, 300] ❌ 错误 (1000x1000相对坐标)

**clean_image_data.py 直接使用JSON中的坐标绘制标记**，假设它们都是1920x1080的真实坐标。

结果：
- Claude不缩放时: 标记正确 ✅
- Claude缩放时: 标记位置错误 ❌
- GPT: 标记位置错误 ❌
- Qwen: 标记位置错误 ❌

## 3. 解决方案

### 方案A: 修改ExecutionRecorder，记录转换后的坐标

**优点**: 
- 简单直接
- JSON中统一存储真实坐标
- clean_image_data.py不需要修改

**缺点**:
- 需要在记录时调用转换逻辑
- 丢失了模型原始输出信息

**实现**:
```python
# 在add_round时，检测tool_calls并转换坐标
for msg in messages:
    if msg.get('role') == 'assistant' and msg.get('tool_calls'):
        for tool_call in msg['tool_calls']:
            if tool_call['function']['name'] in ['computer_use', 'computer']:
                args = json.loads(tool_call['function']['arguments'])
                if 'coordinate' in args:
                    # 根据model_name转换坐标
                    args['coordinate'] = convert_to_real_coordinate(
                        args['coordinate'], 
                        model_name,
                        scale_x, scale_y
                    )
                    tool_call['function']['arguments'] = json.dumps(args)
```

### 方案B: 修改clean_image_data.py，根据模型类型转换坐标

**优点**:
- 保留模型原始输出
- 更完整的数据记录

**缺点**:
- clean_image_data.py需要知道模型类型和缩放信息
- 逻辑更复杂

**实现**:
```python
def extract_action_from_path(data, path):
    # ... 现有代码 ...
    
    # 获取模型名称
    model_name = agent_data.get('model_name', '')
    
    # 获取坐标并转换
    coordinate = action_info.get('coordinate')
    if coordinate:
        if 'claude' in model_name.lower():
            # 检查是否缩放
            if has_scaling_info:
                coordinate = [
                    int(coordinate[0] * scale_x),
                    int(coordinate[1] * scale_y)
                ]
        elif 'gpt' in model_name.lower() or 'qwen' in model_name.lower():
            # 相对坐标转换
            coordinate = [
                int(coordinate[0] / 1000 * 1920),
                int(coordinate[1] / 1000 * 1080)
            ]
    
    return {
        'action': action_info.get('action'),
        'coordinate': coordinate,
        'text': action_info.get('text')
    }
```

## 4. 推荐方案

**推荐方案A** - 在ExecutionRecorder记录时转换坐标

原因:
1. JSON作为最终数据存储，应该存储标准化的真实坐标
2. clean_image_data.py、时间轴可视化等工具都不需要关心模型细节
3. 实现相对简单，一次修改解决所有问题
4. 未来添加新模型时，只需在action parser中处理转换即可

## 5. 实际验证

从当前日志 execution_record_20251226_144048.json:
```json
"coordinate": [660, 266]
```

这个坐标值分析:
- 660/1920 = 34.4% 水平位置
- 266/1080 = 24.6% 垂直位置
- 在屏幕左上区域

**结论**: 这是真实的1920x1080坐标 ✅

**为什么正确？**
因为当前测试使用的是Claude模型，且:
- screenshot_compression 默认为 False，或
- 图片尺寸 ≤ max_screenshot_size

所以 Claude 没有缩放图片，scale_x = scale_y = 1.0，
模型直接看1920x1080的图片，返回的就是真实坐标！

## 6. 需要修复的情况

如果未来启用以下配置，标记会出问题:

1. **Claude 启用压缩**:
   ```python
   agent = ClaudeComputerUseAgent(
       screenshot_compression=True,
       max_screenshot_size=768  # 触发缩放
   )
   ```
   → JSON会记录缩放图坐标，标记位置错误

2. **使用 GPT 模型**:
   → JSON会记录1000x1000相对坐标，标记位置错误

3. **使用 Qwen 模型**:
   → JSON会记录1000x1000相对坐标，标记位置错误

## 7. 修复建议

立即实施方案A:

1. 在 `ExecutionRecorder.add_round()` 中添加坐标转换逻辑
2. 需要传入模型类型和缩放信息
3. 将所有坐标统一转换为1920x1080真实坐标后再记录
4. 确保所有模型的JSON输出坐标一致

这样可以保证:
- ✅ 标记始终准确
- ✅ 不同模型输出一致
- ✅ 可视化工具无需修改
- ✅ 未来添加新模型无影响
"""
