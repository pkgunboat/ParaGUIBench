# OSWorld Script Tasks - Evaluation Config Download Guide

## OSWorld Script Task UIDs (from map.txt)

The following 18 tasks need evaluation configs downloaded:

1. c7c1e4c3-9e92-4eba-a4b8-689953975ea4
2. ce2b64a2-ddc1-4f91-8c7d-a88be7121aac
3. eb303e01-261e-4972-8c07-c9b4e7a4922a
4. da52d699-e8d2-4dc5-9191-a2199e0b6a9b
5. aceb0368-56b8-4073-b70e-3dc9aee184e0
6. 47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5
7. 337d318b-aa07-4f4f-b763-89d9a2dd013f
8. 2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e
9. b5062e3e-641c-4e3a-907b-ac864d2e7652
10. 67890eb6-6ce5-4c00-9e3d-fb4972699b06
11. a82b78bb-7fde-4cb3-94a4-035baf10bcf0
12. 7e287123-70ca-47b9-8521-47db09b69b14
13. 881deb30-9549-4583-a841-8270c65f2a17
14. 5df7b33a-9f77-4101-823e-02f863e1c1ae
15. df67aebb-fb3a-44fd-b75b-51b6012df509
16. 6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a
17. 3e3fc409-bff3-4905-bf16-c968eee3f807
18. d1acdb87-bb67-4f30-84aa-990e56a09c92

## Download Sources

### Option 1: HuggingFace Datasets
- Dataset: xlangai/OSWorld
- Path: evaluation_examples/{domain}/{uid}.json

### Option 2: OSWorld GitHub
- Repository: https://github.com/xlang-ai/OSWorld
- Path: evaluation_examples/examples/{domain}/{uid}.json

## Evaluation Config Structure

Each task JSON contains:
```json
{
  "id": "task-id",
  "instruction": "...",
  "evaluator": {
    "func": "function_name",
    "result": {...},
    "expected": {...}
  },
  "metric": "metric_function",
  "result_getter": "getter_name",
  "expected_getter": "getter_name"
}
```

## Evaluation Functions Location

The evaluation functions are already in the codebase at:
- desktop_env/evaluators/metrics/*.py
