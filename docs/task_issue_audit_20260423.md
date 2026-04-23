# 任务问题排查记录（2026-04-23）

本文档汇总 2026-04-23 对 `seed-1.8 gui-only` 消融实验中“原始已经受到环境、数据准备或 pipeline 包装层问题影响”的任务清单。

参考运行：
- `logs/ablation_20260423_015743`

口径说明：
- 这里只记录已经定位到执行链路、任务数据分发、WebMall 站点状态或 pipeline 包装层漂移的问题。
- 不把纯 agent 推理错误、正常执行但答案做错、以及可能受到影响但无法证明被直接阻断的任务算进“原始受影响任务”。
- 后续扫描中发现、但不在这次原始 run 子集里的问题，会单独放到文末“后续扩展发现”。

## 1. 原始受影响任务总表

按上述口径，本次原始 run 中确认受影响的任务共 `26` 个：

### A. 文件分发 / Hugging Face tree 下载链路异常 `13`
- `InformationRetrieval-FileSearch-Readonly-001`
- `InformationRetrieval-FileSearch-ReadonlyPPT-003`
- `InformationRetrieval-FileSearch-ReadonlyWord-001`
- `InformationRetrieval-FileSearch-ReadonlyWord-002`
- `InformationRetrieval-FileSearch-ReadonlyWord-003`
- `Operation-FileOperate-BatchOperationPPT-002`
- `Operation-FileOperate-BatchOperationWord-001`
- `Operation-FileOperate-BatchOperationExcel-005`
- `Operation-FileOperate-BatchOperation-001`
- `Operation-FileOperate-BatchOperationPPT-001`
- `Operation-FileOperate-CombinationDocs-001`
- `Operation-FileOperate-CombinationDocs-002`
- `Operation-FileOperate-BatchOperationExcel-001`

### B. SearchWrite pipeline 包装层漂移 `3`
- `Operation-FileOperate-SearchAndWrite-004`
- `Operation-WebOperate-SearchAndWrite-001`
- `Operation-FileOperate-SearchAndWrite-001`

### C. WebMall `shop1 / 9081` 维护页污染 `10`
- `Operation-OnlineShopping-AddToCart-001`
- `Operation-OnlineShopping-CheapestProductSearch-004`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-003`
- `Operation-OnlineShopping-FindSubstitutes-001`
- `Operation-OnlineShopping-FindCompatibleProducts-001`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-006`
- `Operation-OnlineShopping-ProductsFulfillingSpecificRequirements-008`
- `Operation-OnlineShopping-SingleProductSearch-012`
- `Operation-OnlineShopping-SingleProductSearch-004`
- `Operation-OnlineShopping-ProductsSatisfyingVagueRequirements-007`

补充说明：
- WebMall 这次一共跑了 `20` 个任务，所有任务 instruction 都会列出 `9081-9084` 四个店铺。
- 但我只把上面这 `10` 个算进“原始受影响任务”，因为它们满足下面两种更严格条件之一：
  - `expected_urls` 本来就包含 `9081` 商品页
  - 任务入口商品页本身就在 `9081`
- 像 `Operation-OnlineShopping-CheapestOfferSpecificRequirements-004` 这类任务，虽然 agent 也访问过坏掉的 `9081`，但正确答案本身不依赖 `9081`，所以不纳入“直接受影响任务”。

## 2. 文件分发 / HF tree 下载链路异常

### 受影响任务
- `InformationRetrieval-FileSearch-Readonly-001`
- `InformationRetrieval-FileSearch-ReadonlyPPT-003`
- `InformationRetrieval-FileSearch-ReadonlyWord-001`
- `InformationRetrieval-FileSearch-ReadonlyWord-002`
- `InformationRetrieval-FileSearch-ReadonlyWord-003`
- `Operation-FileOperate-BatchOperationPPT-002`
- `Operation-FileOperate-BatchOperationWord-001`
- `Operation-FileOperate-BatchOperationExcel-005`
- `Operation-FileOperate-BatchOperation-001`
- `Operation-FileOperate-BatchOperationPPT-001`
- `Operation-FileOperate-CombinationDocs-001`
- `Operation-FileOperate-CombinationDocs-002`
- `Operation-FileOperate-BatchOperationExcel-001`

### 原始现象
- agent 明确报告找不到应有的 Word / PPT / Excel / 图片文件。
- 部分任务在 `shared` 中只看到了一个 HTML 文件。
- 代表性执行记录：
  - `InformationRetrieval-FileSearch-Readonly-001` 写到 “the shared folder only has that one HTML file”
  - `Operation-FileOperate-BatchOperation-001` 明确说 `shared` 里只有 HTML 文件，没有山峰图片
  - `InformationRetrieval-FileSearch-ReadonlyWord-001`、`Operation-FileOperate-BatchOperationExcel-001` 都直接因为“找不到文件”判失败

### 根因
- `src/stages/run_QA_pipeline_parallel.py` 的 `_download_task_files_on_vm_with_ip()` 之前有三处问题：
  - 没有稳定优先命中宿主机侧缓存
  - `tree` 目录下载后没有稳定扁平化到 `/home/user/shared/`
  - Hugging Face `tree` API 失败时，会错误回退成“直接下载网页本身”，导致 VM 拿到的是 HTML 页面而不是真文件

### 修复
- 新增统一任务缓存模块：
  - [src/stages/task_data_cache.py](/home/yuzedong/code/ParaGUIBench/src/stages/task_data_cache.py:108)
- 修复 QA 下载链路：
  - [src/stages/run_QA_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_QA_pipeline_parallel.py:96)
  - [src/stages/run_QA_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_QA_pipeline_parallel.py:210)
  - [src/stages/run_QA_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_QA_pipeline_parallel.py:251)
- 补齐 self-operation 侧对 `task_uid` 的透传：
  - [src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py:1072)
- 新增预下载脚本：
  - [scripts/prefetch_task_data.py](/home/yuzedong/code/ParaGUIBench/scripts/prefetch_task_data.py:1)

### 当前状态
- 本地缓存链路已打通。
- 已实测缓存出真实 `.docx` / `.zip` 文件。
- 还没对这 `13` 个任务做完整回归重跑。

## 3. SearchWrite pipeline 包装层漂移

### 受影响任务
- `Operation-FileOperate-SearchAndWrite-004`
- `Operation-WebOperate-SearchAndWrite-001`
- `Operation-FileOperate-SearchAndWrite-001`

### 原始现象
- `Operation-FileOperate-SearchAndWrite-004` 最终回答是 “I cannot find the table with missing data.”
- `Operation-WebOperate-SearchAndWrite-001` 最终直接向用户要餐馆名单
- `Operation-FileOperate-SearchAndWrite-001` 最终说页面上没有给出应有的链接

这三类现象分别对应两条包装层问题：
- OnlyOffice 共享链接没有注入 instruction，agent 根本不知道该打开在线表格
- OSWorld SearchWrite 任务被错误送进 OnlyOffice / 错误初始化链路

### 根因

#### 3.1 OnlyOffice 共享链接未注入 instruction
- `SearchWritePipeline.stage_execute()` 原先直接传原始 `task_config`
- 没有像并行版那样先调用 `_build_instruction_with_share_urls()`
- 直接影响非 OSWorld 的 SearchWrite 任务，原始 run 中体现在 `Operation-FileOperate-SearchAndWrite-004`

#### 3.2 OSWorld SearchWrite 任务被错误归入 OnlyOffice 逻辑
- `Operation-WebOperate-SearchAndWrite-001`
- `Operation-FileOperate-SearchAndWrite-001`

这两类任务本质上是 OSWorld 脚本任务，不应该先去做 OnlyOffice Stage0，也不应该走 QA 风格初始化。

### 修复
- 注入共享链接：
  - [src/pipelines/searchwrite_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/searchwrite_pipeline.py:183)
- SearchWrite 初始化按任务类型分流：
  - [src/pipelines/searchwrite_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/searchwrite_pipeline.py:164)
- OSWorld SearchWrite 跳过 OnlyOffice Stage0：
  - [src/pipelines/searchwrite_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/searchwrite_pipeline.py:123)
  - [src/pipelines/searchwrite_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/searchwrite_pipeline.py:130)

### 当前状态
- `searchwrite_pipeline.py` 已通过静态编译。
- 尚未针对这 `3` 个原始受影响任务回归重跑。

## 4. WebMall `shop1 / 9081` 维护页污染

### 受影响任务
- `Operation-OnlineShopping-AddToCart-001`
- `Operation-OnlineShopping-CheapestProductSearch-004`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-003`
- `Operation-OnlineShopping-FindSubstitutes-001`
- `Operation-OnlineShopping-FindCompatibleProducts-001`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-006`
- `Operation-OnlineShopping-ProductsFulfillingSpecificRequirements-008`
- `Operation-OnlineShopping-SingleProductSearch-012`
- `Operation-OnlineShopping-SingleProductSearch-004`
- `Operation-OnlineShopping-ProductsSatisfyingVagueRequirements-007`

### 原始现象
- 运行当时的 `9081` 实际不是正常商城，而是一个默认 WordPress / WooCommerce 维护页站点。
- 多个 WebMall 执行记录里，agent 明确写到：
  - `E-Store Athletes was under maintenance`
  - `E-Store Athletes is actually a blog under construction`

### 为什么只算这 10 个

#### 4.1 正确答案本来就依赖 `9081`
- `Operation-OnlineShopping-AddToCart-001`
- `Operation-OnlineShopping-CheapestProductSearch-004`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-003`
- `Operation-OnlineShopping-FindCompatibleProducts-001`
- `Operation-OnlineShopping-CheapestOfferVagueRequirements-006`
- `Operation-OnlineShopping-ProductsFulfillingSpecificRequirements-008`
- `Operation-OnlineShopping-SingleProductSearch-012`
- `Operation-OnlineShopping-SingleProductSearch-004`
- `Operation-OnlineShopping-ProductsSatisfyingVagueRequirements-007`

这些任务的 `expected_urls` / `answer` 中本来就包含 `http://10.1.110.114:9081/product/...`，所以 `9081` 坏掉会直接导致召回缺失。

#### 4.2 任务入口商品页本身就在 `9081`
- `Operation-OnlineShopping-FindSubstitutes-001`

这个任务虽然最终正确答案不在 `9081`，但起始参考商品页本身就在 `9081`。原站是维护页时，agent 连基准商品都拿不到。

### 根因
- 在线的并不是当前仓库 `docker/docker-compose.yaml` 拉起的 WebMall，而是一套旧 `docker_all` 栈。
- 旧栈里的 `shop1 / 9081` 卷已经漂移成默认 WordPress 站点，标题为 `User's blog`，并开启了 WooCommerce `coming soon`。

### 修复

#### 4.1 运行环境修复
- 已恢复旧栈 `shop1 / 9081` 的原始店铺数据
- 当前 `9081` 已恢复为 `E-Store Athletes`

#### 4.2 代码层修复
- WebMall 自检不再把维护页当成“站点正常”：
  - [src/stages/run_webmall_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_webmall_pipeline.py:1)
- `setup_webmall.sh` 改为按 compose 服务名查容器，不再硬编码旧容器名：
  - [scripts/setup_webmall.sh](/home/yuzedong/code/ParaGUIBench/scripts/setup_webmall.sh:1)
- 排障文档已补充：
  - [docs/troubleshooting.md](/home/yuzedong/code/ParaGUIBench/docs/troubleshooting.md:1)

### 当前状态
- `9081` 运行环境已经恢复。
- 代码层面也已经防止未来把维护页误判成健康商城。
- 这 `10` 个任务还没在修复后统一重跑。

## 5. 本次已实施的代码修复

与这份清单直接相关的改动包括：
- [src/stages/task_data_cache.py](/home/yuzedong/code/ParaGUIBench/src/stages/task_data_cache.py:1)
- [scripts/prefetch_task_data.py](/home/yuzedong/code/ParaGUIBench/scripts/prefetch_task_data.py:1)
- [src/stages/run_QA_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_QA_pipeline_parallel.py:96)
- [src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py:1050)
- [src/pipelines/searchwrite_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/searchwrite_pipeline.py:115)
- [src/stages/run_webmall_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/stages/run_webmall_pipeline.py:1)
- [scripts/setup_webmall.sh](/home/yuzedong/code/ParaGUIBench/scripts/setup_webmall.sh:1)
- [docs/troubleshooting.md](/home/yuzedong/code/ParaGUIBench/docs/troubleshooting.md:1)

## 6. 当前验证情况

已完成：
- `python -m py_compile src/stages/task_data_cache.py`
- `python -m py_compile src/stages/run_QA_pipeline_parallel.py`
- `python -m py_compile src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py`
- `python -m py_compile src/parallel_benchmark/eval/osworld_evaluator.py`
- `python -m py_compile src/pipelines/searchwrite_pipeline.py`
- `python -m py_compile src/pipelines/webnavigate_pipeline.py`
- `python -m py_compile src/stages/run_webmall_pipeline.py`
- `bash -n scripts/setup_webmall.sh`

未完成：
- 还没有基于修复后的代码和已恢复的 `9081` 重新跑一次完整的 `seed-1.8 gui-only` 消融实验。
- 最小回归集合应至少覆盖：
  - 文件分发类 `13` 个任务
  - SearchWrite 类 `3` 个任务
  - WebMall `9081` 直接依赖类 `10` 个任务

## 7. 后续扩展发现（不计入本次原始受影响任务统计）

下面这些问题是在后续代码扫描中确认的，但它们不属于这次原始 run 的“已受影响任务清单”：

### 7.1 OSWorld config 未执行
- `Operation-FileOperate-CombinationDocs-009`
- `Operation-FileOperate-CombinationDocs-012`
- `Operation-FileOperate-SearchAndWrite-005`

这些任务的真实输入文件定义在 OSWorld evaluator JSON 的 `config` 字段里，不在 `benchmark_dataset/<task_uid>`。
已在 [src/parallel_benchmark/eval/osworld_evaluator.py](/home/yuzedong/code/ParaGUIBench/src/parallel_benchmark/eval/osworld_evaluator.py:676) 和 [src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py](/home/yuzedong/code/ParaGUIBench/src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py:1050) 中补齐执行逻辑。

### 7.2 WebNavigate 初始化后未重新打开 Chrome
- 包装层问题已修：
  - [src/pipelines/webnavigate_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/webnavigate_pipeline.py:113)
  - [src/pipelines/webnavigate_pipeline.py](/home/yuzedong/code/ParaGUIBench/src/pipelines/webnavigate_pipeline.py:119)
- 但本次原始 run 里的 `WebNavigate-001/002` 都是通过的，所以不算进“原始受影响任务”。

### 7.3 coding 任务残留
- `Operation-FileOperate-Coding-001`
- `Operation-FileOperate-Coding-002`
- `Operation-FileOperate-Coding-003`
- `Operation-FileOperate-Coding-004`
- `Operation-FileOperate-Coding-005`

这些 JSON 物理文件仍在仓库里，但当前实验不应再纳入它们。
