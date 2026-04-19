"""
task_scanner 模块的单元测试

使用 pytest 的 tmp_path fixture 创建临时任务 JSON 文件，
覆盖全部扫描、pipeline 过滤、ID 过滤、组合过滤等场景。
"""

import json
import os

import pytest

from task_scanner import scan_unified_tasks, VALID_PIPELINES

# ---------------------------------------------------------------------------
# 测试用的任务数据，覆盖 5 种 pipeline
# ---------------------------------------------------------------------------

SAMPLE_TASKS = [
    # --- qa pipeline (task_type == "QA", 不含 OnlineShopping) ---
    {
        "task_id": "InformationRetrieval-FileSearch-Readonly-001",
        "task_uid": "uid-qa-001",
        "task_type": "QA",
        "task_tag": "FileSearch",
        "instruction": "Find the paper",
        "answer": "paper3",
    },
    {
        "task_id": "InformationRetrieval-WebSearch-ConditionalSearch-001",
        "task_uid": "uid-qa-002",
        "task_type": "QA",
        "task_tag": "WebSearch",
        "instruction": "Search for Nobel Prize winner",
        "answer": "Akira Yoshino",
    },
    # --- webmall pipeline (task_id 含 OnlineShopping) ---
    {
        "task_id": "Operation-OnlineShopping-AddToCart-001",
        "task_uid": "uid-webmall-001",
        "task_type": "QA",
        "task_tag": "OnlineShopping",
        "instruction": "Add item to cart",
        "answer": "done",
    },
    {
        "task_id": "Operation-OnlineShopping-Purchase-002",
        "task_uid": "uid-webmall-002",
        "task_type": "",
        "task_tag": "OnlineShopping",
        "instruction": "Purchase item",
        "answer": "",
    },
    # --- operation pipeline (Operation-FileOperate-*, 不含 SearchAndWrite) ---
    {
        "task_id": "Operation-FileOperate-BatchOperation-001",
        "task_uid": "uid-op-001",
        "task_type": "",
        "task_tag": "FileOperate",
        "instruction": "Batch rename files",
        "answer": "",
    },
    {
        "task_id": "Operation-FileOperate-CombinationDocs-001",
        "task_uid": "uid-op-002",
        "task_type": "",
        "task_tag": "FileOperate",
        "instruction": "Combine documents",
        "answer": "",
    },
    # --- webnavigate pipeline (含 WebOperate, 不含 SearchAndWrite) ---
    {
        "task_id": "Operation-WebOperate-WebNavigate-001",
        "task_uid": "uid-webnav-001",
        "task_type": "",
        "task_tag": "WebOperate",
        "instruction": "Navigate to settings",
        "answer": "",
    },
    {
        "task_id": "Operation-WebOperate-Settings-001",
        "task_uid": "uid-webnav-002",
        "task_type": "",
        "task_tag": "WebOperate",
        "instruction": "Change browser settings",
        "answer": "",
    },
    # --- searchwrite pipeline (含 SearchAndWrite，跨 FileOperate 和 WebOperate) ---
    {
        "task_id": "Operation-FileOperate-SearchAndWrite-001",
        "task_uid": "uid-sw-001",
        "task_type": "",
        "task_tag": "FileOperate",
        "instruction": "Search and write to file",
        "answer": "",
    },
    {
        "task_id": "Operation-WebOperate-SearchAndWrite-001",
        "task_uid": "uid-sw-002",
        "task_type": "",
        "task_tag": "WebOperate",
        "instruction": "Search web and write",
        "answer": "",
    },
]


@pytest.fixture
def tasks_dir(tmp_path):
    """
    在临时目录下创建所有测试用任务 JSON 文件，并附带一个应被跳过的 id_mapping.json。

    输入参数:
        tmp_path: pytest 内置 fixture，提供临时目录路径

    输出返回值:
        str — 临时任务目录的路径
    """
    for task in SAMPLE_TASKS:
        filepath = tmp_path / f"{task['task_id']}.json"
        filepath.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

    # 写入应被跳过的 id_mapping.json
    mapping_file = tmp_path / "id_mapping.json"
    mapping_file.write_text(json.dumps({"mapping": "data"}), encoding="utf-8")

    return str(tmp_path)


# ===========================================================================
# 1. 扫描全部任务
# ===========================================================================


class TestScanAll:
    """测试不带任何过滤条件的全量扫描"""

    def test_scan_all_returns_all_tasks(self, tasks_dir):
        """扫描全部任务，应返回 SAMPLE_TASKS 中的所有任务（不含 id_mapping）"""
        results = scan_unified_tasks(tasks_dir)
        assert len(results) == len(SAMPLE_TASKS)

    def test_scan_all_sorted_by_task_id(self, tasks_dir):
        """返回结果应按 task_id 字母序排序"""
        results = scan_unified_tasks(tasks_dir)
        task_ids = [r[0] for r in results]
        assert task_ids == sorted(task_ids)

    def test_scan_all_skips_id_mapping(self, tasks_dir):
        """id_mapping.json 应被跳过"""
        results = scan_unified_tasks(tasks_dir)
        task_ids = {r[0] for r in results}
        assert "id_mapping" not in task_ids


# ===========================================================================
# 2. 按 pipeline 过滤
# ===========================================================================


class TestPipelineFilter:
    """测试按 pipeline 名称过滤"""

    def test_qa_pipeline(self, tasks_dir):
        """qa pipeline: task_type=='QA' 且不含 OnlineShopping"""
        results = scan_unified_tasks(tasks_dir, pipeline="qa")
        task_ids = {r[0] for r in results}
        assert task_ids == {
            "InformationRetrieval-FileSearch-Readonly-001",
            "InformationRetrieval-WebSearch-ConditionalSearch-001",
        }
        # OnlineShopping 的 QA 任务不应出现
        assert all("OnlineShopping" not in tid for tid in task_ids)

    def test_webmall_pipeline(self, tasks_dir):
        """webmall pipeline: task_id 含 OnlineShopping"""
        results = scan_unified_tasks(tasks_dir, pipeline="webmall")
        task_ids = {r[0] for r in results}
        assert task_ids == {
            "Operation-OnlineShopping-AddToCart-001",
            "Operation-OnlineShopping-Purchase-002",
        }

    def test_operation_pipeline(self, tasks_dir):
        """operation pipeline: Operation-FileOperate-* 且不含 SearchAndWrite"""
        results = scan_unified_tasks(tasks_dir, pipeline="operation")
        task_ids = {r[0] for r in results}
        assert task_ids == {
            "Operation-FileOperate-BatchOperation-001",
            "Operation-FileOperate-CombinationDocs-001",
        }
        # SearchAndWrite 不应出现
        assert all("SearchAndWrite" not in tid for tid in task_ids)

    def test_webnavigate_pipeline(self, tasks_dir):
        """webnavigate pipeline: 含 WebOperate 且不含 SearchAndWrite"""
        results = scan_unified_tasks(tasks_dir, pipeline="webnavigate")
        task_ids = {r[0] for r in results}
        assert task_ids == {
            "Operation-WebOperate-WebNavigate-001",
            "Operation-WebOperate-Settings-001",
        }

    def test_searchwrite_pipeline(self, tasks_dir):
        """searchwrite pipeline: 含 SearchAndWrite（跨 FileOperate 和 WebOperate）"""
        results = scan_unified_tasks(tasks_dir, pipeline="searchwrite")
        task_ids = {r[0] for r in results}
        assert task_ids == {
            "Operation-FileOperate-SearchAndWrite-001",
            "Operation-WebOperate-SearchAndWrite-001",
        }

    def test_searchwrite_covers_both_tags(self, tasks_dir):
        """SearchAndWrite 应同时覆盖 FileOperate 和 WebOperate 两个 Tag"""
        results = scan_unified_tasks(tasks_dir, pipeline="searchwrite")
        tags = {r[2].get("task_tag") for r in results}
        assert "FileOperate" in tags
        assert "WebOperate" in tags

    def test_invalid_pipeline_raises(self, tasks_dir):
        """传入非法 pipeline 名称应抛出 ValueError"""
        with pytest.raises(ValueError, match="未知的 pipeline"):
            scan_unified_tasks(tasks_dir, pipeline="invalid_pipeline")

    def test_all_pipelines_are_disjoint(self, tasks_dir):
        """5 个 pipeline 的结果互不重叠，合集等于全部任务"""
        all_ids = set()
        for p in VALID_PIPELINES:
            ids = {r[0] for r in scan_unified_tasks(tasks_dir, pipeline=p)}
            # 不应与已有集合重叠
            assert all_ids.isdisjoint(ids), f"pipeline '{p}' 与其他 pipeline 有重叠"
            all_ids.update(ids)
        # 合集应等于全部任务
        total = {r[0] for r in scan_unified_tasks(tasks_dir)}
        assert all_ids == total


# ===========================================================================
# 3. 按 allowed_ids 过滤
# ===========================================================================


class TestAllowedIdsFilter:
    """测试按 task_id 白名单过滤"""

    def test_filter_by_single_id(self, tasks_dir):
        """只允许一个 task_id"""
        allowed = {"Operation-FileOperate-BatchOperation-001"}
        results = scan_unified_tasks(tasks_dir, allowed_ids=allowed)
        assert len(results) == 1
        assert results[0][0] == "Operation-FileOperate-BatchOperation-001"

    def test_filter_by_multiple_ids(self, tasks_dir):
        """允许多个 task_id"""
        allowed = {
            "InformationRetrieval-FileSearch-Readonly-001",
            "Operation-WebOperate-Settings-001",
        }
        results = scan_unified_tasks(tasks_dir, allowed_ids=allowed)
        assert len(results) == 2
        assert {r[0] for r in results} == allowed

    def test_filter_by_nonexistent_id(self, tasks_dir):
        """允许列表中的 ID 不存在时返回空"""
        results = scan_unified_tasks(tasks_dir, allowed_ids={"nonexistent-task"})
        assert len(results) == 0


# ===========================================================================
# 4. 按 allowed_uids 过滤
# ===========================================================================


class TestAllowedUidsFilter:
    """测试按 task_uid 白名单过滤"""

    def test_filter_by_single_uid(self, tasks_dir):
        """只允许一个 task_uid"""
        results = scan_unified_tasks(tasks_dir, allowed_uids={"uid-op-001"})
        assert len(results) == 1
        assert results[0][0] == "Operation-FileOperate-BatchOperation-001"

    def test_filter_by_multiple_uids(self, tasks_dir):
        """允许多个 task_uid"""
        results = scan_unified_tasks(tasks_dir, allowed_uids={"uid-qa-001", "uid-sw-002"})
        assert len(results) == 2
        ids = {r[0] for r in results}
        assert "InformationRetrieval-FileSearch-Readonly-001" in ids
        assert "Operation-WebOperate-SearchAndWrite-001" in ids


# ===========================================================================
# 5. 返回格式验证
# ===========================================================================


class TestReturnFormat:
    """验证返回的三元组格式"""

    def test_tuple_structure(self, tasks_dir):
        """每个元素应为 (task_id, task_path, config) 三元组"""
        results = scan_unified_tasks(tasks_dir)
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 3
            task_id, task_path, config = item
            assert isinstance(task_id, str)
            assert isinstance(task_path, str)
            assert isinstance(config, dict)

    def test_task_path_is_absolute(self, tasks_dir):
        """task_path 应为绝对路径"""
        results = scan_unified_tasks(tasks_dir)
        for _, task_path, _ in results:
            assert os.path.isabs(task_path)

    def test_task_path_exists(self, tasks_dir):
        """task_path 指向的文件应存在"""
        results = scan_unified_tasks(tasks_dir)
        for _, task_path, _ in results:
            assert os.path.isfile(task_path)

    def test_config_contains_task_id(self, tasks_dir):
        """config 字典中应包含 task_id 字段，且与三元组第一个元素一致"""
        results = scan_unified_tasks(tasks_dir)
        for task_id, _, config in results:
            assert config["task_id"] == task_id


# ===========================================================================
# 6. 空目录
# ===========================================================================


class TestEmptyDirectory:
    """测试空目录的行为"""

    def test_empty_dir_returns_empty(self, tmp_path):
        """空目录应返回空列表"""
        results = scan_unified_tasks(str(tmp_path))
        assert results == []

    def test_only_id_mapping_returns_empty(self, tmp_path):
        """只有 id_mapping.json 时也应返回空列表"""
        mapping_file = tmp_path / "id_mapping.json"
        mapping_file.write_text(json.dumps({"a": 1}))
        results = scan_unified_tasks(str(tmp_path))
        assert results == []


# ===========================================================================
# 7. pipeline 与 allowed_ids 组合过滤
# ===========================================================================


class TestCombinedFilters:
    """测试多个过滤条件组合使用"""

    def test_pipeline_and_allowed_ids(self, tasks_dir):
        """pipeline + allowed_ids 同时生效：取交集"""
        # qa pipeline 有 2 个任务，但 allowed_ids 只允许其中 1 个
        results = scan_unified_tasks(
            tasks_dir,
            pipeline="qa",
            allowed_ids={"InformationRetrieval-FileSearch-Readonly-001"},
        )
        assert len(results) == 1
        assert results[0][0] == "InformationRetrieval-FileSearch-Readonly-001"

    def test_pipeline_and_allowed_ids_no_overlap(self, tasks_dir):
        """pipeline 与 allowed_ids 无交集时返回空"""
        # allowed_ids 是 operation 的任务，但 pipeline 过滤为 qa
        results = scan_unified_tasks(
            tasks_dir,
            pipeline="qa",
            allowed_ids={"Operation-FileOperate-BatchOperation-001"},
        )
        assert len(results) == 0

    def test_pipeline_and_allowed_uids(self, tasks_dir):
        """pipeline + allowed_uids 同时生效"""
        results = scan_unified_tasks(
            tasks_dir,
            pipeline="webmall",
            allowed_uids={"uid-webmall-001"},
        )
        assert len(results) == 1
        assert results[0][0] == "Operation-OnlineShopping-AddToCart-001"

    def test_all_three_filters(self, tasks_dir):
        """pipeline + allowed_ids + allowed_uids 三者同时生效"""
        results = scan_unified_tasks(
            tasks_dir,
            pipeline="qa",
            allowed_ids={"InformationRetrieval-FileSearch-Readonly-001"},
            allowed_uids={"uid-qa-001"},
        )
        assert len(results) == 1

    def test_all_three_filters_mismatch(self, tasks_dir):
        """三个条件中任一不满足则被排除"""
        # uid 不匹配
        results = scan_unified_tasks(
            tasks_dir,
            pipeline="qa",
            allowed_ids={"InformationRetrieval-FileSearch-Readonly-001"},
            allowed_uids={"uid-qa-002"},  # 不是 Readonly-001 的 uid
        )
        assert len(results) == 0
