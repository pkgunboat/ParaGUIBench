# Holo3 GUI Agent (单步定位 MVP)

ParaGUIBench 内部的 Holo3 接入实现。本目录目前只覆盖 H Company Holo3 协议中
**单步预测**所需的最小子集，验证模型本身能不能在给定截图上定位目标控件；
多步 ChatHistory、OSWorld 多轮 loop 等后续再补。

## 文件结构

| 文件 | 作用 |
|---|---|
| `tools.py`   | 11 个工具的 Pydantic 模型 + `Step` 顶层包装（discriminated union） |
| `prompts.py` | 系统提示模板，把 `Step.model_json_schema()` 内嵌到 `<output_format>` |
| `parser.py`  | `Step` → `pyautogui` 代码 + 0-1000 坐标反归一化（`step_to_pixel` / `step_to_pyautogui`） |
| `client.py`  | OpenAI 兼容客户端；三级降级：`structured_outputs` → `response_format=json_object` → 纯文本+正则 |
| `agent.py`   | `Holo3Agent.predict(instruction, obs)` 单步入口，返回 `PredictResult` |

## 快速用法

```python
from src.mm_agents.holo3.agent import Holo3Agent

agent = Holo3Agent(
    base_url="http://127.0.0.1:8000/v1",          # 也可 10.1.110.114:8000
    model="Holo3-35B-A3B.Q4_K_S.gguf",
    platform="macOS",
)
agent.reset()

with open("some.png", "rb") as f:
    png_bytes = f.read()

result = agent.predict("Open Google Chrome", {"screenshot": png_bytes})
print(result.response)       # "[click@px=(...)] ..."
print(result.actions)        # ["import pyautogui; pyautogui.click(...)"]
print(result.step.tool_call) # 已校验的 Pydantic 对象
```

## 跑活测脚本

```bash
# 默认走 10.1.110.114:8000；本机有 SSH 转发到 127.0.0.1:8000 也行
HOLO3_BASE_URL=http://127.0.0.1:8000/v1 \
    python tests/holo3/test_localize_chrome.py
```

输出会落在 `tests/holo3/annotated_chrome.png`，原图上画红色十字 + 圆圈 + 标签。

## 跑单元测试

```bash
python -m pytest tests/holo3/test_tools.py tests/holo3/test_parser.py -v
```

## 已知与 spec 的偏差

1. **平台**：spec 默认 Ubuntu，本实现的系统提示默认 `macOS`，是为了配合
   ParaGUIBench 的 macOS 截图。可由 `Holo3Agent(platform=...)` 覆盖。
2. **服务端**：spec 推荐 vLLM；当前用 llama.cpp GGUF (Q4_K_S)。`client.py`
   会先按 vLLM 的 `extra_body.structured_outputs.json` 试，失败再降级，理论上
   两种后端都能用。
3. **多步历史**：暂未实现 `ChatHistory` / 图片预算。`predict()` 每次都是
   单步独立调用。后续接入 OSWorld 时再补。
