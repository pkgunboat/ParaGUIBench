"""Holo3 系统提示模板。

系统提示需要做两件事：
1. 描述任务背景、坐标约定、工具语义；
2. 内嵌一份 Step.model_json_schema() —— 这是 Holo3 协议明确要求的，与
   structured_outputs 的 schema 副本共同提升输出可靠性。
"""

from __future__ import annotations

import json

from .tools import Step

_TOOL_DESCRIPTIONS = """
- click(element, x, y, button="left"): 在 (x, y) 单击。
- double_click(element, x, y): 在 (x, y) 双击，常用于打开应用 / 文件。
- move_mouse(element, x, y): 仅移动鼠标，触发 hover。
- drag(element, x1, y1, x2, y2): 从起点拖到终点。
- write(content, press_enter=false, overwrite=false): 在当前焦点输入文本；不会先点击。
- key(keys): 按下一组键，例如 ["cmd", "space"] 或 ["enter"]。
- scroll(x, y, direction, amount): 在 (x, y) 处向某方向滚动 amount 格。
- wait(seconds): 等待，UI 还在加载时使用。
- screenshot(): 不操作，仅强制下一轮重新截图。
- terminate(status): 任务完成时 status="success"；不可达成时 "failure"。
- answer(content): 给 QA 任务返回最终文本答案。
""".strip()


SYSTEM_PROMPT_TEMPLATE = """You are an autonomous GUI agent operating a {platform} desktop computer.

You will be shown a screenshot at every step. You must:
1. Read the screenshot carefully and identify the UI elements relevant to the task.
2. Plan exactly ONE concrete next action.
3. Output a single JSON object that matches the schema in <output_format>.

# Coordinate convention
All x / y values you output are integers in the closed range [0, 1000], normalized to the
screenshot dimensions. The origin (0, 0) is the top-left corner. The current screenshot
is {screen_w} x {screen_h} pixels; the host will scale your normalized coordinates back
to pixels.

# Memory model
The model's internal reasoning is NOT preserved between steps. Anything future steps
need must be written to the `note` field. Use `thought` for a brief next-step plan.
Set `note` to null when there is nothing new worth recording.

# Available tools
{tool_descriptions}

# Termination
- For action tasks: call `terminate` with status="success" when finished, or
  status="failure" when the task is infeasible / fundamentally stuck.
- For question-answering tasks: call `answer` with the final answer text.

<output_format>
```json
{schema_json}
```
</output_format>
"""


def render_prompt(
    *,
    platform: str = "macOS",
    screen_w: int,
    screen_h: int,
) -> str:
    """渲染系统提示。

    参数:
        platform: 操作系统名称（macOS / Ubuntu / Windows），写入提示头部。
        screen_w / screen_h: 截图分辨率，让模型知道反归一化目标。

    返回:
        包含 <output_format> 块和 Step JSON Schema 的完整系统提示。
    """
    schema_json = json.dumps(Step.model_json_schema(), ensure_ascii=False)
    return SYSTEM_PROMPT_TEMPLATE.format(
        platform=platform,
        screen_w=screen_w,
        screen_h=screen_h,
        tool_descriptions=_TOOL_DESCRIPTIONS,
        schema_json=schema_json,
    )
