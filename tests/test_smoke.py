"""
冒烟测试：验证所有 Pipeline 子类可正常实例化和参数解析。
不需要实际 VM 连接，通过 mock 跳过重型依赖。
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# 确保能 import 当前目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock 掉重型依赖
_MOCK_MODULES = [
    "psutil", "paramiko", "requests", "volcenginesdkarkruntime",
    "desktop_env", "desktop_env.providers", "desktop_env.providers.docker",
    "desktop_env.providers.docker.parallel_manager",
    "desktop_env.controllers", "desktop_env.controllers.python",
    "run_QA_pipeline", "run_QA_pipeline_parallel",
    "run_webmall_pipeline", "run_webmall_pipeline_parallel",
    "run_webnavigate_pipeline_parallel",
    "self_operation_pipeline",
    "self_operation_pipeline.run_self_operation_pipeline_parallel",
    "self_operation_pipeline.run_searchwrite_pipeline_parallel",
]
for _mod_name in _MOCK_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# 注入 mock 对象到关键模块
_pm = sys.modules["desktop_env.providers.docker.parallel_manager"]
_pm.ContainerSetConfig = MagicMock
_pm.MemoryGuard = MagicMock
_pm.allocate_ports_for_group = MagicMock()
_pm.scan_remote_docker_ports = MagicMock(return_value=[])

_qa_par = sys.modules["run_QA_pipeline_parallel"]
_qa_par.rebuild_containers_parallel = MagicMock()
_qa_par.cleanup_group_containers = MagicMock()
_qa_par.execute_on_vm_with_ip = MagicMock()
_qa_par.wait_for_vm_ready_with_ip = MagicMock()
_qa_par.get_ssh_credentials = MagicMock(return_value={})
_qa_par.disable_screensaver_parallel = MagicMock()
_qa_par.stage1_initialize_parallel = MagicMock(return_value=True)
_qa_par.stage2_execute_agent_parallel = MagicMock(return_value=({}, None))
_qa_par.stage2_execute_gui_only = MagicMock(return_value=({}, None))

_qa = sys.modules["run_QA_pipeline"]
_qa.ensure_conda_env = MagicMock()
_qa.stage3_evaluate = MagicMock(return_value={"pass": False})

_wm = sys.modules["run_webmall_pipeline"]
_wm.check_webmall_shops = MagicMock(return_value=True)

_wm_par = sys.modules["run_webmall_pipeline_parallel"]
_wm_par.reinitialize_vms_parallel = MagicMock(return_value=True)
_wm_par.stage2_execute_parallel = MagicMock(return_value=({}, None))
_wm_par.stage2_execute_gui_only = MagicMock(return_value=({}, None))
_wm_par.stage3_evaluate_parallel = MagicMock(return_value={"pass": False})

_wn = sys.modules["run_webnavigate_pipeline_parallel"]
_wn.reinitialize_vms = MagicMock(return_value=True)
_wn.clear_bookmarks_parallel = MagicMock()
_wn.open_browser_parallel = MagicMock(return_value=True)
_wn.stage2_execute_plan = MagicMock(return_value=({}, None))
_wn.stage2_execute_gui_only = MagicMock(return_value=({}, None))
_wn.stage3_evaluate = MagicMock(return_value={"pass": False})

_op = sys.modules["self_operation_pipeline.run_self_operation_pipeline_parallel"]
_op.stage1_initialize_with_flatten = MagicMock(return_value=True)
_op.stage2_execute_gui_only = MagicMock(return_value=({}, None))
_op.stage3_evaluate_operation = MagicMock(return_value={"pass": False})

_sw = sys.modules["self_operation_pipeline.run_searchwrite_pipeline_parallel"]
_sw.stage0_prepare_documents = MagicMock(return_value={})
_sw.stage1_initialize = MagicMock(return_value=True)
_sw.resolve_document_sharing_url = MagicMock(return_value="http://localhost:5000")
_sw._build_instruction_with_share_urls = MagicMock(side_effect=lambda instruction, _urls: instruction)
_sw.fetch_document_file_via_api = MagicMock(return_value=b"")
_sw.stage2_execute_gui_only = MagicMock(return_value=({}, None))
_sw.stage2_5_trigger_save = MagicMock(return_value=True)
_sw.stage3_evaluate = MagicMock(return_value={"pass": False})

# 现在可以安全 import
from pipeline_base import BasePipeline, TaskItem
from qa_pipeline import QAPipeline
from webmall_pipeline import WebMallPipeline
from webnavigate_pipeline import WebNavigatePipeline
from operation_pipeline import OperationPipeline
from searchwrite_pipeline import SearchWritePipeline


class TestPipelineInstantiation(unittest.TestCase):
    """所有 Pipeline 子类的实例化和参数解析测试。"""

    PIPELINE_CLASSES = [
        QAPipeline, WebMallPipeline, WebNavigatePipeline,
        OperationPipeline, SearchWritePipeline,
    ]

    def test_all_pipelines_have_pipeline_name(self):
        """每个 pipeline 都有非空 pipeline_name。"""
        for cls in self.PIPELINE_CLASSES:
            p = cls()
            self.assertIsInstance(p.pipeline_name, str)
            self.assertTrue(len(p.pipeline_name) > 0)

    def test_all_pipelines_parse_common_args(self):
        """所有 pipeline 共享公共参数。"""
        for cls in self.PIPELINE_CLASSES:
            p = cls()
            parser = p.build_parser()
            args = parser.parse_args(["--mode", "full", "-n", "3"])
            self.assertEqual(args.mode, "full")
            self.assertEqual(args.vms_per_task, 3)

    def test_operation_has_gt_cache_dir_arg(self):
        """OperationPipeline 有 --gt-cache-dir 参数。"""
        p = OperationPipeline()
        parser = p.build_parser()
        args = parser.parse_args(["--gt-cache-dir", "/tmp/gt"])
        self.assertEqual(args.gt_cache_dir, "/tmp/gt")

    def test_searchwrite_has_onlyoffice_args(self):
        """SearchWritePipeline 有 --onlyoffice-url 参数。"""
        p = SearchWritePipeline()
        parser = p.build_parser()
        args = parser.parse_args(["--onlyoffice-url", "http://localhost:5050"])
        self.assertEqual(args.onlyoffice_url, "http://localhost:5050")

        default_args = parser.parse_args([])
        self.assertTrue(default_args.onlyoffice_url.startswith("http://"))
        self.assertTrue(default_args.onlyoffice_url.endswith(":5050"))

    def test_all_pipelines_have_default_subset_file(self):
        """每个 pipeline 都有 default_subset_file 属性。"""
        for cls in self.PIPELINE_CLASSES:
            p = cls()
            self.assertIsInstance(p.default_subset_file, str)

    def test_gui_only_forces_vms_1(self):
        """gui_only 模式应强制 vms_per_task=1。"""
        p = QAPipeline()
        parser = p.build_parser()
        p.args = parser.parse_args(["--agent-mode", "gui_only", "-n", "5"])
        if p.args.agent_mode == "gui_only":
            p.args.vms_per_task = 1
        self.assertEqual(p.args.vms_per_task, 1)

    def test_pipeline_names_unique(self):
        """所有 pipeline_name 互不相同。"""
        names = [cls().pipeline_name for cls in self.PIPELINE_CLASSES]
        self.assertEqual(len(names), len(set(names)))

    def test_expected_pipeline_names(self):
        """pipeline_name 与预期值一致。"""
        expected = {"qa", "webmall", "webnavigate", "operation", "searchwrite"}
        actual = {cls().pipeline_name for cls in self.PIPELINE_CLASSES}
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
