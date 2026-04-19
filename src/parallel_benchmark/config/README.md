# API 配置说明

## 统一配置文件

所有 API keys 和 base URLs 现在统一在 `api_config.py` 中管理。

### 使用方法

```python
from config.api_config import get_api_config, get_model_name

# 获取 API 配置
api_config = get_api_config("deerapi")
api_key = api_config["api_key"]
base_url = api_config["base_url"]

# 获取模型名称
model = get_model_name("plan_agent")  # "gpt-5-2025-08-07"
```

### 支持的 Provider

- `"deerapi"`: DeerAPI (GPT-5 等模型)
- `"claude"`: Claude API

### 修改 API Key

只需修改 `api_config.py` 中的配置：

```python
DEERAPI_CONFIG = {
    "api_key": "your-new-key-here",
    "base_url": "https://api.deerapi.com/v1/",
}
```

### 已更新的文件

以下文件已更新为使用统一配置：

1. `process/run_plan_agent_thought_action.py`
2. `process/test_parallel_translate_attention.py`
3. `parallel_agents/plan_agent_thought_action.py`
4. `parallel_agents/plan_agent_multi_code.py`
5. `parallel_agents/gui_agent.py`
6. `parallel_agents_as_tools/gui_agent_as_tool.py`
7. `parallel_agents_as_tools/gpt_gui_agent_as_tool.py`

### 当前 API Key

```
新 API Key: ${OPENAI_API_KEY}
旧 API Key: ${OPENAI_API_KEY} (已停用)
```

## 优势

✅ **集中管理**: 所有 API 配置在一个文件中  
✅ **易于维护**: 修改一次，全局生效  
✅ **环境变量支持**: 可通过环境变量覆盖默认配置  
✅ **多 Provider 支持**: 轻松切换不同的 API 提供商
