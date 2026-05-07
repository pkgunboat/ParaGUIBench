# ParaGUIBench 论文附录材料

> 本文件汇总 Benchmark 数据集、评价器与 Baseline Agent 系统的全量实现细节，可直接用于论文方法章 / 实验设置 / 附录。所有数字均来自仓库实际计数与代码核验，路径基于 `src/parallel_benchmark/` 与 `src/stages/`。

---

## 一、Benchmark 全景

### 1.1 任务规模与 Pipeline 划分

任务总数 **238**，全部存放在 `src/parallel_benchmark/tasks/*.json`。按 task_id 前缀划分到 5 个 pipeline：

| Pipeline       | 任务前缀                              | 任务数 | 默认 subset                  | 备注                                       |
| -------------- | ------------------------------------- | ------ | ---------------------------- | ------------------------------------------ |
| QA             | `InformationRetrieval-*`              | 77     | 19（`qa_subset.txt`）        | WebSearch + FileSearch + VisualSearch      |
| WebMall        | `Operation-OnlineShopping-*`          | 91     | 20（`webmall_subset_20.txt`）| 4 个 WooCommerce 商城 9081–9084            |
| Operation      | `Operation-FileOperate-*`             | 47     | 12（`operation_subset.txt`） | Word/Excel/PPT/Coding/CombinationDocs      |
| WebNavigate    | `Operation-WebOperate-WebNavigate-*` 与 `-Settings-*` | 13 | 4（`webnavigate_subset.txt`） | 书签验证类                                 |
| SearchWrite    | `Operation-FileOperate-SearchAndWrite-*` 与 `Operation-WebOperate-SearchAndWrite-*` | 10 | 5（`searchwrite_subset.txt`） | OnlyOffice 在线编辑或 OSWorld 文件填写     |
| **合计**       |                                       | **238**|                              |                                            |

> 路径：`src/parallel_benchmark/tasks/subsets/*.txt` —— ablation 模式默认仅跑 subset；`--mode full` 跑全量。

### 1.2 子类（task_tag）问题分布

由 task 文件名末尾的 `task_tag` 字段标注。

**QA Pipeline（77）**

| 子类 | 数量 |
| --- | ---: |
| WebSearch-StatisticSearch | 27 |
| WebSearch-VisualSearch | 19 |
| WebSearch-ConditionalSearch | 10 |
| WebSearch-MultiHopSearch | 8 |
| FileSearch-ReadonlyPPT | 5 |
| FileSearch-ReadonlyWord | 4 |
| FileSearch-Readonly | 3 |
| VisualSearch-Video | 1 |

**WebMall Pipeline（91）**

| 子类 | 数量 |
| --- | ---: |
| SingleProductSearch | 12 |
| CheapestProductSearch | 12 |
| ProductsFulfillingSpecificRequirements | 11 |
| CheapestOfferSpecificRequirements | 10 |
| ProductsSatisfyingVagueRequirements | 8 |
| EndToEnd | 8 |
| Checkout | 8 |
| AddToCart | 7 |
| CheapestOfferVagueRequirements | 6 |
| FindCompatibleProducts | 5 |
| FindSubstitutes | 4 |

**Operation Pipeline（47）**

| 子类 | 数量 |
| --- | ---: |
| CombinationDocs | 15 |
| BatchOperationWord | 12 |
| BatchOperationExcel | 9 |
| Coding | 5 |
| BatchOperationPPT | 3 |
| BatchOperation | 2 |
| Settings | 1 |

**WebNavigate Pipeline（13）**：WebNavigate 10 + Settings 3
**SearchWrite Pipeline（10）**：FileOperate-SearchAndWrite 9 + WebOperate-SearchAndWrite 1

### 1.3 任务 JSON 字段约定

四种 `task_type` 决定走哪条评价分支：

| task_type | 评价路由 | 关键字段 |
| --- | --- | --- |
| `QA` + 默认 | `eval/file_search_readonly_evaluator.py` | `answer`, `accepted_answers` |
| `QA` + `evaluator_path: evaluators/string_url_evaluator.py` | `StringEvaluator` | `expected_urls`, `answer_type ∈ {string, cart, checkout}` |
| `OSWorld脚本` | `eval/operation_evaluator.py` 走 `eval_rules` 数组；或 `eval/osworld_scripts/*.json` | `eval_rules: [{check, file_pattern, params, weight, description}]` |
| `self` | 各 pipeline 自定义评价器（如 `eval/webnavigate_bookmark_evaluator.py`） | `answer`（含正则识别的 ground-truth URL） |

通用字段：`task_id`、`task_uid`、`instruction`（输入指令）、`prepare_script_path`（HF Hub 资源 URL，用于 VM 初始化）、`original_task_id`。

### 1.4 评价器（Evaluators）

#### (A) QA — `FileSearchReadonlyEvaluator`

源：`src/parallel_benchmark/eval/file_search_readonly_evaluator.py`

输出格式硬约束：Agent 必须在 instruction 指定下输出 `<answer>VALUE</answer>`。

**6 级匹配链**（任一通过即 pass）：

1. 答案归一化（去引号、弯引号→直引号、剥 `.pdf/.docx/.pptx` 后缀、`key : value` → `key:value`）后**精确匹配**
2. **去括号内容**后精确匹配（处理 `Malaysia (not Myanmar)`）
3. **KV 单值子串**：`brand:Samsung` 命中 `brand:Samsung Galaxy`
4. **关键词全匹配**：分词后所有关键词以 stem 前缀出现
5. **区间匹配**：`up:2.3±0.7` 命中 `up:2.16` 等含误差物理量
6. **包含匹配（降级）**：双向子串包含时 `score=0.5, pass=False`

**多值答案**（reference 用分号分隔）：F1 score（precision + recall），`pass = (F1 == 1.0)`。

**特殊返回**：`INSUFFICIENT_EVIDENCE` / `Aborted` / `Fatal Error` → `score = 0`。

#### (B) WebMall — 三种子评价器

源：`src/stages/webmall_eval_assets/`

| answer_type | 评价器                                  | 检查对象                                                | 通过条件                                                                                   |
| ----------- | --------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `string`    | `string_evaluator.py`                   | Agent 报告的 product URL 列表                           | URL 归一化（去 `http://`、去末尾 `/`、小写）后**精确匹配** `expected_urls`                 |
| `cart`      | `cart_evaluator_from_at.py` / `vm_cart_evaluator.py` | 4 个商城购物车页面的 Accessibility Tree | 从 AT 提取商品名 → WooCommerce slug 转换（小写、`&`→` amp `、非字母数字→`-`）→ 与期望 slug 求匹配率 |
| `checkout`  | `checkout_evaluator_from_at.py`         | Chrome history 里最近的 `order-received` URL 对应页面    | 校验订单号 / 产品 slug / 姓名 / 邮箱 / 地址五项字段，逐项分                                |

**得分**：string 单 URL 0/1，多 URL 用 F1；cart/checkout 是 0~1 部分得分。**不依赖 Agent 自报**——cart/checkout 直接从 VM UI 状态回读，杜绝幻觉作弊。

#### (C) Operation — `OperationEvaluator` + 检查函数注册表

源：`src/parallel_benchmark/eval/operation_evaluator.py` 与 `eval/operation_checks/`

执行逻辑：

```python
for rule in task["eval_rules"]:
    files = glob(result_dir + "/" + rule["file_pattern"])  # *.docx / *.xlsx 等
    scores = [CHECK_REGISTRY[rule["check"]](file, rule["params"])["score"] for file in files]
    rule_score = mean(scores)
total_score = Σ(rule_score × rule.weight) / Σ(weight)
pass = (total_score >= 1.0)
```

**44 个原子检查函数（CHECK_REGISTRY）按文件类型分类**：

- **Word（17 个）**：`check_font_name`、`check_line_spacing`、`check_heading_hierarchy`（H1→H2→H3 不跳级）、`check_has_toc`、`check_first_line_indent`、`check_max_consecutive_blank_lines`、`check_vowels_colored_red`、`check_uppercase_words_have_parentheses`、`check_heading_style_exists`、`check_has_table`、`check_highlighted_words_capitalized`、`check_misspelled_words_highlighted`、`check_heading_colors_different`、`check_image_name_matches_doc`、`check_docx_word_count`、`check_docx_has_hyperlink`、`check_batchword002_tab_indent`
- **Excel（21 个）**：`check_cell_value`、`check_header_bold`、`check_column_alignment`、`check_sort_order`、`check_has_sum_row`、`check_cell_contains_string`、`check_values_are_decimals`、`check_negative_values_colored`、`check_cells_filled`（最常用，检查区域填充）、`check_sequential_numbers`、`check_multi_cell_values`、`check_no_na_values` 等 + `check_batchexcel00X_*` 任务专用变体
- **PPT（3 个）**：`check_slide_transition`、`check_text_not_overflow`、`check_batchppt002_bounds_overlap`
- **目录级（4 个）**：`check_files_exist`、`check_files_in_same_folder`、`check_html_files_for_xlsx`、`check_named_files_exist`

**评分**：每条 rule 0~1，加权总和 ≥ 1.0 才 pass；得分支持部分得分。

#### (D) WebNavigate — `BookmarkEvaluator`

源：`src/parallel_benchmark/eval/webnavigate_bookmark_evaluator.py`

```python
REGEX_PATTERNS["Operation-WebOperate-WebNavigate-001"] = {
    "patterns": [
        r"accuweather.*manchester",
        r"accuweather.*manchester.*air-quality-index",
    ],
    "expected_count": 2,
}
```

读 VM 内 Chrome `Bookmarks` 文件 → 递归收 URL → 大小写不敏感正则匹配，每个 URL 仅计数一次，`score = matched / expected_count`，`pass = (score == 1.0)`。

#### (E) SearchWrite — OSWorld 脚本评估

源：`src/parallel_benchmark/eval/osworld_scripts/{task_uid}.json`

每个任务有专门的 JSON 评价脚本，定义文件比对、单元格值检查、命令执行验证；返回 `{pass: bool, score: 0/1, reason: str}`。

### 1.5 统计指标

主指标 + 次级指标，由 `src/pipelines/master_table.py` 统一收集到 51 列 CSV：

- **任务级**：`score`(0~1)、`pass`(bool)、`interrupted`(bool)、`plan_rounds`、`gui_rounds_total`、`gui_steps_sequential`、`token_plan` / `token_gui` / `token_total`、`cost_usd`、`elapsed_time_sec`
- **Pipeline 级聚合**（`src/pipelines/report_generator.py`）：通过率 `pass/total`、平均分、Σtoken、Σcost、Σtime
- **全局对照报告**（`master_report.py`）：8 个 Sheet —— Raw / Main / Plan-Ablation / GUI-Ablation / GUI-Only / Parallelism / Oracle / Coverage

**特色指标——并行度**：

```python
parallelism = gui_rounds_total / gui_steps_sequential
# 1.0 = 完全串行；N.0 = N 路真并行；上界由 vms_per_task 决定
```

衡量 Plan Agent 任务分解质量的关键指标。

---

## 二、Baseline Agent

### 2.1 系统架构（两层）

**第一层 — Plan Agent（协调层）**

- 主实现：`src/parallel_benchmark/parallel_agents/plan_agent_thought_action.py`（thought-action 范式 + parallel tool calls）
- 兼容旧版：`plan_agent.py`（基于 JSON plan + ThreadPoolExecutor）
- 默认模型：`gpt-5-2025-08-07`，可切 Claude Sonnet 4.5 / Doubao Seed 1.8 / DeepSeek
- **不直接操作 GUI**，只通过 `call_gui_agent()` 工具派发

**第二层 — N 个 GUI Agent（执行层）**

每个跑在独立 QEMU VM 内（默认 N=5，上限 20）。每个模型一份适配器：

- `gpt54_gui_agent.py`：GPT-5.4-mini，OpenAI Responses API + computer-use（有状态 `previous_response_id`）
- `seed_1_8_gui_test.py`：Doubao Seed-1.8，三层 fallback 解析（tool_calls → XML `<function_calls>` → 纯文本 `Action: ...`）
- `qwen3_gui_agent.py`：Qwen3-VL，绝对坐标系 [0,1000]² + `smart_resize` 缩放
- `claude_computer_use_agent.py`：Anthropic computer_use tool
- `kimi_gui_agent.py` / `doubao_seed_gui_agent.py`：其他厂商

### 2.2 Plan Agent 的 Tool-Call Schema

定义于 `plan_agent_thought_action.py:336-380`：

```json
{
  "type": "function",
  "function": {
    "name": "call_gui_agent",
    "description": "Dispatch a task to a specific GUI Agent. Each agent has independent VM/browser/cookies.",
    "parameters": {
      "type": "object",
      "properties": {
        "task_description": {
          "type": "string",
          "description": "Clear description of the GUI task (2-3 sentences)"
        },
        "agent_id": {
          "type": "integer",
          "enum": [1, 2, 3, 4, 5],
          "description": "Which GUI Agent (1-N). SAME agent_id across rounds preserves session state."
        }
      },
      "required": ["task_description", "agent_id"]
    }
  }
}
```

`enum` 是动态的：根据 `num_agents` 配置生成 `[1..N]`。

**Plan Agent 的可选动作只有三种**：

1. 发起一组并行 `call_gui_agent` tool_calls（默认 `parallel_tool_calls=True`，可用 `PLAN_SERIAL_GUI_CALLS=1` 强制串行）
2. 输出 `<answer>...</answer>` 文本 → **唯一终止信号**
3. 纯文本无工具调用 → 通常是分析；连续 3 轮无 tool_calls 视为隐式完成

**动态 re-plan**：每轮的 messages 历史包含上轮全部 GUI Agent 返回结果，Plan Agent 据此自由调整下一步分发；不存在固定的预定义 plan tree。依赖关系实时从 thought 的 XML 标签中正则解析（`_parse_dependencies_from_xml`），失败则启发式分析（`_analyze_dependencies`）。

**Plan Agent 输出 schema**（返回给 evaluator）：

```python
{
    "success": bool,
    "final_answer": str,
    "execution_log": {
        "task": str, "start_time": iso8601,
        "rounds": [
            {"round", "thought", "tool_calls", "results", "dependencies", "token_usage"}
        ]
    },
    "execution_record": {...},
    "total_rounds": int,
    "elapsed_time": float,
    "token_usage": {
        "plan_agent": {"prompt_tokens", "completion_tokens", "total_tokens"},
        "gui_agent":  {...},
        "plan_agent_model": str,
        "gui_agent_model": str
    }
}
```

### 2.3 GUI Agent 的返回 Schema

`gui_agent.py:166-228` 定义统一接口：

```python
def predict(instruction: str, obs: Dict) -> Tuple[str, List[str], str]:
    """
    obs = {"screenshot": <base64 PNG>}
    返回:
      final_thought:  自由文本推理
      actions:        List[str]，标准化 pyautogui 动作字符串
      pyautogui_code: 可执行 Python，或特殊标记 "DONE"/"WAIT"/"FAIL"/"DONE:<answer>"
    """
```

**统一 Action Space**（解析后）：`click / double_click / right_click / drag / scroll / type / hotkey / press / wait / finished / fail`

**返回三种状态**：

- 正常动作 → 返回 pyautogui 代码，外层执行后传新 screenshot 给下一轮
- `finished(content="...")` → `pyautogui_code = "DONE"` 或 `"DONE:<answer>"`，回控给 Plan Agent
- 主动失败：`fail()` → `"FAIL"`；超过 `max_trajectory_length=50` → 框架强制 `"FAIL"`
- `wait()` → `"WAIT"`，等待 UI 稳定后下轮再决策

**多模型坐标差异**：

- Qwen3-VL：相对坐标 [0,1000]²，外层按真实分辨率反归一化
- GPT-5.4 / Claude：基于 1280×720，按 1920×1080 缩放（`pyautogui_code_parser.py`）
- Doubao Seed：[0,1000]² 相对坐标

### 2.4 能力边界

| 维度           | Plan Agent                                                                 | GUI Agent                                                                          |
| -------------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **看到**       | 仅 `task` 原文 + 每轮 tool 返回的文本结果摘要（success/fail + result + steps 数） + 自身 thought 历史 | 当前 + 历史 N=5 张 PNG 截图                                                        |
| **看不到**     | 浏览器画面、GUI 中间过程、其他 agent 的 thought、VM 文件系统               | Plan Agent 的策略、其他 agent 的状态、控制台 stderr                                |
| **可做**       | 任务分解、并行派发、聚合、动态 re-plan、输出 final answer / `INSUFFICIENT_EVIDENCE` | 鼠标（点击/拖拽/滚动）、键盘、热键、`finished/fail/wait`                            |
| **不可做**     | 直接执行 GUI 动作、读 VM 文件、调用外部 API、给自己重启 VM                 | 跨 VM 通信（仅通过 `/home/user/shared/` 文件共享）、安装软件、跳出沙盒；网络受 VM 防火墙约束 |
| **状态持久性** | messages 历史是唯一长期记忆                                                | 同一 agent_id 跨 round 保留浏览器 tab/cookies/登录态/打开的文档；不同 agent_id 完全隔离 |

### 2.5 完整执行伪代码（贴合实现）

```python
# 入口：plan_agent_thought_action.py:execute_task
def execute_task(task, context=None,
                 max_rounds=10,                # Plan Agent 最多 10 轮 thought-action
                 max_rounds_per_subtask=50,    # 单次 GUI Agent 调用最多 50 步
                 timeout_per_subtask=0,        # 单子任务超时（0=不限）
                 task_timeout=7200):           # 整任务硬超时 2 小时
    start = time.time()
    messages = [
        system(plan_agent_system_prompt),       # 动态注入 num_agents=N
        user(task + optional_context),
    ]
    self._gui_steps_used = 0
    self.gui_step_budget = 200                  # 全局 GUI 步数预算

    for round_num in range(max_rounds):
        # ── (1) 整任务超时检查
        if task_timeout > 0 and time.time() - start >= task_timeout:
            log("[TIMEOUT] 强制结束"); break

        # ── (2) Plan Agent 调用 LLM
        response = vlm.chat.completions.create(
            model="gpt-5-2025-08-07", messages=messages,
            tools=[call_gui_agent_schema],
            parallel_tool_calls=not serial_gui_calls,
            max_tokens=8000, temperature=0.0, seed=LLM_SEED)
        msg = response.choices[0].message
        accumulate_token_usage(response.usage)

        # ── (3) 终止信号 1：<answer> 标签
        if msg.content and (m := re.search(r"<answer>(.*?)</answer>", msg.content, re.S | re.I)):
            ans = _local_clean_answer(m.group(1))
            # 防护：若上一轮所有子 agent 都失败，强制覆盖为 INSUFFICIENT_EVIDENCE
            ans = _maybe_override_with_insufficient_evidence(execution_log, ans)
            recorder.set_final_answer(ans); break

        # ── (4) 终止信号 2：连续 3 轮无 tool_calls
        if not msg.tool_calls:
            consecutive_no_tool_calls += 1
            if consecutive_no_tool_calls >= 3:
                recorder.set_final_answer(msg.content or ""); break
            messages.append(msg); continue
        consecutive_no_tool_calls = 0

        # ── (5) GUI step budget 限制
        remaining = gui_step_budget - self._gui_steps_used
        if remaining <= 0: break
        effective_max_rounds = min(max_rounds_per_subtask, remaining)

        # ── (6) 并行执行所有 tool_calls（ThreadPoolExecutor）
        tool_results = self._execute_tool_calls(
            msg.tool_calls, effective_max_rounds, timeout_per_subtask, round_log)
        # 每个 tool_call 内部：
        #   gui_agent = registry.get(model_type)  # gpt54 / seed18 / qwen3 / claude
        #   for step in range(effective_max_rounds):  # 单 agent 最多 50 步
        #       screenshot = vm.capture()
        #       thought, actions, code = gui_agent.predict(task_description, {"screenshot": screenshot})
        #       if code in ("DONE", "FAIL", "WAIT"): break
        #       vm.execute_pyautogui(code)
        #   return {"status": "success" | "failure", "result": ..., "steps": [...]}

        # ── (7) API 致命错误：连续 2 轮全部子 agent 因 quota / 403 失败 → 终止
        if all(_is_api_fatal_error(r) for r in tool_results):
            consecutive_api_fatal += 1
            if consecutive_api_fatal >= 2: return {"status": "api_fatal_error"}

        # ── (8) 更新 budget + 依赖图 + 写回 messages
        self._gui_steps_used += sum(len(r["steps"]) for r in tool_results)
        deps = self._parse_dependencies_from_xml(msg.content) or self._analyze_dependencies(...)
        for tr in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tr.id,
                "content": "✓ ..." if tr.success else "✗ Failed: ...",
            })

    # ── (9) 收尾
    return {
        "success": ..., "final_answer": ...,
        "total_rounds": round_num + 1,
        "elapsed_time": time.time() - start,
        "token_usage": {...},
    }
```

### 2.6 边界情况处理

#### (a) 何时输出 final answer？

**唯一触发条件**：Plan Agent 的 LLM 输出包含 `<answer>...</answer>` 标签（`plan_agent_thought_action.py:614-632`）。

- 提取后经 `_local_clean_answer` 本地清洗（去引号、去括号补充、剥嵌套标签），**不再调 LLM**。
- GUI Agent 自身用 `finished(content=...)` 仅是把答案"上报"给 Plan Agent，Plan Agent 仍要决定是否输出 `<answer>`。

#### (b) 是否允许「不可完成」？

**允许，但通过强约束机制实现**：

- GUI Agent 可以主动 `fail()` → 返回 `status="failure"` 给 Plan Agent。
- Plan Agent 没有显式的 `give_up` tool；它只能在 `<answer>` 里写 `INSUFFICIENT_EVIDENCE`。
- **关键防护**（`_maybe_override_with_insufficient_evidence`，行 120-141）：若最近一轮所有子 agent 都失败但 LLM 仍想编造答案，框架强制覆盖为 `INSUFFICIENT_EVIDENCE`，避免幻觉。
- evaluator 视 `INSUFFICIENT_EVIDENCE` / `Aborted` / `Fatal Error` 关键字直接判 score=0。

#### (c) Plan Agent 与 GUI Agent 如何协调？

- **同步阻塞**：每轮所有 tool_calls 通过 `ThreadPoolExecutor` 并行启动，但 `as_completed` **等所有完成后才回到 LLM** 进行下一轮 thought（不是流式）。
- **通信渠道**：仅通过 OpenAI messages 数组里的 `tool` role 消息回传文本结果；Plan Agent 看不到原始截图。
- **跨 agent 协作**：通过 VM 内挂载的 `/home/user/shared/`（SSHFS）共享文件；Plan Agent 在 prompt 里明确指示 agent 读写共享目录。
- **失败重试**：Plan Agent 自主决定下一轮是否重派同一 agent_id（无自动重试），可以换 agent_id 或换策略。

#### (d) 超过 max_round 怎么处理？

默认值（`plan_agent_thought_action.py:427-430` + 行 260）：

- `max_rounds = 10`（Plan Agent 最多 10 轮 thought-action）
- `max_rounds_per_subtask = 50`（单 GUI Agent 单次调用最多 50 步）
- `gui_step_budget = 200`（全局 GUI 步数硬上限）
- `task_timeout = 7200s = 2 小时`（整任务墙钟超时）

超限处理：

1. **超 max_rounds**：`for` 循环自然结束 → 不主动 break → recorder 走 `_finalize` 路径；若 recorder 已设过 final_answer 就用之，否则用最后一轮 thought 作为答案（多半 fail）。
2. **超 gui_step_budget**：跳过本轮 tool_calls，注入 tool 消息 `"GUI step budget exhausted. Please summarize results and finish."`，逼迫 Plan Agent 立即给 `<answer>`。
3. **超 task_timeout**：`break` 主循环并打印 `[TIMEOUT] 已完成 X/Y 轮，GUI 步数: A/B`。
4. **GUI Agent 超 50 步**：`gui_agent.py:406` 强制返回 `"FAIL"`，被 Plan Agent 视为该 agent 失败。

所有超限场景都仍**让 evaluator 跑完检查 VM 的最终状态**——cart/checkout/operation 评价器从 VM 状态回读，不依赖 Agent 的"完成"宣告，timeout 也可能拿到部分分。

### 2.7 论文级补充细节

**Token & Cost 追踪**（`plan_agent_thought_action.py:148-238`）：

```python
MODEL_PRICING = {
    "gpt-5-2025-08-07":           {"input": 2.50,  "output": 10.00},   # $/M tokens
    "claude-sonnet-4-5-20250929": {"input": 3.00,  "output": 15.00},
    "doubao-seed-1-8-251228":     {"input": 0.11,  "output": 0.28},
    "deepseek-chat":              {"input": 0.27,  "output": 1.10},
}
```

逐轮记录、分 plan/gui 计、最后打印总成本报告。

**截图与图像处理**：

- 格式 PNG，base64 传输
- `smart_resize` 保证 H, W 整除 28，总像素 ∈ [78,400, 12,845,056]，最大宽高比 200
- `history_n = 5`（仅保留最近 5 张以防 token 爆炸）

**并发隔离**：

- `max_concurrent_vms = 20`，`per_vm_memory_gib = 4`，`per_vm_cpus = 2`（`configs/deploy.example.yaml`）
- 每个 VM 一个独立 Docker 容器内运行 QEMU；浏览器、cookies、登录态独立
- Plan Agent 通过 agent_id 轮询调度（`next_gui_vm_index`）

**Determinism**：`LLM_SEED` 固定，`temperature=0.0`，`parallel_tool_calls=True`（除非环境变量 `PLAN_SERIAL_GUI_CALLS=1`）。

**Plan Agent System Prompt 关键段**（`plan_agent_prompt_thought_action.py`）：

```
# ROLE: You are a Task Planning Agent. You have no direct access to any browser or desktop.
# ENVIRONMENT: You have N GUI Agents (1-N), each isolated VM, action limit 50/call,
#   session persistence across rounds, shared dir /home/user/shared/, no agent memory.
# PRINCIPLES:
#   1. Understand before acting     2. Parallel by default
#   3. Full context transfer        4. Concise instructions (2-3 sentences)
#   5. Clear boundaries             6. Stop when done (<answer> terminates)
#   7. Resolve contradictions sparingly
# ANSWER FORMAT: <answer>...</answer>
```

设计哲学：**不给 few-shot，不写示例**——靠强模型自身推理；只声明架构事实和七条原则。

### 2.8 与 OSWorld baseline 的关键差异

| 维度           | OSWorld         | ParaGUIBench Baseline                                          |
| -------------- | --------------- | -------------------------------------------------------------- |
| Agent 数       | 1（线性）       | N=1~20（默认 5）并行                                           |
| 任务分解       | 隐式（agent 自规划）| Plan Agent 显式 thought-action 派发                            |
| Tool 调用      | 单一 computer_use | `call_gui_agent` 二级派发                                      |
| 跨 agent 共享  | —               | SSHFS `/home/user/shared/`                                     |
| 答案终止       | 模型自定        | `<answer>` 强制语法 + `INSUFFICIENT_EVIDENCE` 防护             |
| 多模型支持     | OpenAI/Anthropic | + Qwen3 / Doubao Seed / Kimi / DeepSeek，三层 fallback 解析    |
| 度量           | success rate    | + parallelism = total_steps / sequential_steps（核心新指标）   |

---

## 三、写论文亮点

可以高亮的方法学卖点：

1. **首个把 Plan-GUI 解耦 + N 路真并行 GUI 派发做完整 benchmark 化**的工作。
2. **`parallelism = gui_rounds_total / gui_steps_sequential`** 是论文最有故事的新指标，能区分 Plan Agent "会不会拆分"。
3. **评价器全部从 VM 真实状态回读**（AT、文件、Chrome history、Bookmarks），而非读 Agent 自报，杜绝幻觉作弊。
4. **`<answer>` 标签 + `INSUFFICIENT_EVIDENCE` 强制覆盖**机制是对 OSWorld 等已有工作的具体改进。
5. **238 任务 × 5 pipeline × 多评价范式**（精确匹配 / F1 / AT-slug / 规则加权 / 正则 / OSWorld 脚本）覆盖广度领先。

附录建议放：完整 evaluator 路由表、44 个 check 函数清单、Plan Agent system prompt 全文、5 个 pipeline 各一条任务 JSON 示例、超参表（max_rounds=10 / subtask=50 / budget=200 / timeout=7200s）。

---

## 附 A：超参速查

| 超参                       | 默认值 | 位置                                                              |
| -------------------------- | ------ | ----------------------------------------------------------------- |
| `max_rounds`               | 10     | `plan_agent_thought_action.py:427`                                |
| `max_rounds_per_subtask`   | 50     | `plan_agent_thought_action.py:428`                                |
| `gui_step_budget`          | 200    | `plan_agent_thought_action.py:260`                                |
| `task_timeout`             | 7200s  | `plan_agent_thought_action.py:430`                                |
| `max_trajectory_length`    | 50     | `gui_agent.py`                                                    |
| `history_n`                | 5      | `configs/agent.example.yaml:16`                                   |
| `temperature` / `seed`     | 0.0 / `LLM_SEED` | `plan_agent_thought_action.py`                          |
| `max_concurrent_vms`       | 20     | `configs/deploy.example.yaml`                                     |
| `per_vm_memory_gib`        | 4      | `configs/deploy.example.yaml`                                     |
| `per_vm_cpus`              | 2      | `configs/deploy.example.yaml`                                     |
| `parallel_tool_calls`      | True   | `plan_agent_thought_action.py:534`（`PLAN_SERIAL_GUI_CALLS=1` 关闭）|

## 附 B：Evaluator 路由速查

| Pipeline    | task_type / answer_type           | 评价器                                                                                  | 通过条件                              | 得分范围                       |
| ----------- | --------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------- | ------------------------------ |
| QA          | `QA` / 默认                       | `eval/file_search_readonly_evaluator.py`                                                | 6 级匹配链任一通过                    | 0/1（单值）；F1（多值）        |
| WebMall     | `QA` / `string`                   | `stages/webmall_eval_assets/string_evaluator.py`                                        | URL 归一化精确匹配                    | 0/1（单 URL）；F1（多 URL）    |
| WebMall     | `QA` / `cart`                     | `stages/webmall_eval_assets/cart_evaluator_from_at.py`                                  | AT slug 匹配率 == 100%                | 0~1 加权                        |
| WebMall     | `QA` / `checkout`                 | `stages/webmall_eval_assets/checkout_evaluator_from_at.py`                              | 5 项字段验证通过率                    | 0~1 字段级                      |
| Operation   | `OSWorld脚本` / `eval_rules`      | `eval/operation_evaluator.py` + 44 个 CHECK_REGISTRY                                    | 加权 rule 总分 ≥ 1.0                  | 0~1 加权                        |
| WebNavigate | `self`                            | `eval/webnavigate_bookmark_evaluator.py`                                                | 正则匹配率 == 100%                    | 0~1 pattern 级                  |
| SearchWrite | `OSWorld脚本` / `evaluator_path`  | `eval/osworld_scripts/{task_uid}.json`                                                  | 脚本定义检查全通过                    | 0/1（脚本定义）                 |
