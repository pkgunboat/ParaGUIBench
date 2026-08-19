# WebMall Eval Assets

该目录用于保存 `run_webmall_pipeline.py` 运行所需的外层仓库可跟踪评测资产副本，避免依赖
`ubuntu_env/extra_docker_env/WebMall/.git`（嵌套仓库）。

当前包含：

- `bookmark_utils.py`：pipeline 实际依赖（清空/读取收藏夹）。
- `task_uid_mapping.json`：任务 UID 映射副本（便于后续扩展评测逻辑）。
- `string_evaluator.py`
- `webmall_task_evaluator.py`
- `vm_cart_evaluator.py`
- `cart_evaluator_from_at.py`
- `checkout_evaluator_from_at.py`
