"""runtime-support-v1 预览支持清单的行为回归测试。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_TOOL_PATH = (
    REPO_ROOT / "scripts" / "benchmark" / "runtime_support_manifest.py"
)
SUPPORT_MANIFEST_PATH = (
    REPO_ROOT / "benchmark" / "manifests" / "runtime-support-v1.json"
)
VALIDATOR_PATH = (
    REPO_ROOT / "scripts" / "benchmark" / "validate_runtime_support.py"
)
GENERATOR_PATH = (
    REPO_ROOT / "scripts" / "benchmark" / "generate_runtime_support.py"
)


def _load_support_tool() -> ModuleType:
    """从独立脚本路径加载 runtime support 公共接口。

    输入参数：
        无；脚本路径由当前仓库根目录推导。
    输出返回值：
        已加载并可调用生成、校验函数的模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_runtime_support_tool",
        SUPPORT_TOOL_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 runtime support 工具")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuntimeSupportManifestTest(unittest.TestCase):
    """验证 canonical 发布状态不会被误写成 runtime 已就绪。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载一次 runtime support 工具供全部测试复用。

        输入参数：
            无。
        输出返回值：
            无；模块保存在测试类属性 ``support_tool``。
        """

        cls.support_tool = _load_support_tool()

    def test_only_live_gate_is_marked_live_validated(self) -> None:
        """确认 233 个已发布任务中只有真实纵向切片标为 live。

        输入参数：
            无。
        输出返回值：
            无；断言 canonical 状态与 runtime 支持状态彼此独立。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = manifest["tasks"]
        live_entries = [
            entry
            for entry in entries
            if entry["support_status"] == "live_validated"
        ]

        self.assertEqual(233, len(entries))
        self.assertTrue(
            all(entry["canonical_status"] == "published" for entry in entries)
        )
        self.assertEqual(
            ["InformationRetrieval-FileSearch-Readonly-001"],
            [entry["task_id"] for entry in live_entries],
        )

    def test_validator_accepts_checked_in_deterministic_manifest(self) -> None:
        """确认独立 validator 接受与 canonical 元数据一致的落盘清单。

        输入参数：
            无。
        输出返回值：
            无；断言校验通过、任务总数与状态计数。
        """

        result = self.support_tool.validate_runtime_support_manifest(
            REPO_ROOT,
            SUPPORT_MANIFEST_PATH,
        )

        self.assertEqual([], result.errors)
        self.assertEqual(233, result.task_count)
        self.assertEqual(
            {"blocked": 232, "live_validated": 1},
            result.status_counts,
        )

    def test_validator_is_independently_runnable(self) -> None:
        """确认校验门禁可由 CI 作为独立脚本直接运行。

        输入参数：
            无。
        输出返回值：
            无；断言脚本成功且只输出无敏感值的状态计数。
        """

        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "--repo-root",
                str(REPO_ROOT),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            "runtime-support-v1 valid: "
            "tasks=233; blocked=232, live_validated=1\n",
            completed.stdout,
        )

    def test_generator_is_deterministic_and_independently_runnable(self) -> None:
        """确认独立生成脚本在任意输出路径产生相同无时间戳清单。

        输入参数：
            无；测试将输出写入临时目录。
        输出返回值：
            无；断言脚本输出与公共生成接口完全一致。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "runtime-support-v1.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR_PATH),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--output",
                    str(output_path),
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            generated = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(
            self.support_tool.build_runtime_support_manifest(REPO_ROOT),
            generated,
        )

    def test_migrated_qa_evaluator_and_asset_blockers_are_derived(self) -> None:
        """确认已迁 QA 不再误报 evaluator blocker，远程资产仍独立阻塞。

        输入参数：
            无。
        输出返回值：
            无；断言阻塞码来自 canonical 元数据且不会混同“未声明资产”。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {
            entry["task_id"]: entry for entry in manifest["tasks"]
        }
        legacy_asset_entry = entries[
            "InformationRetrieval-FileSearch-Readonly-002"
        ]
        asset_free_entry = entries[
            "InformationRetrieval-WebSearch-ConditionalSearch-001"
        ]

        self.assertEqual(
            "legacy_remote_reference",
            legacy_asset_entry["asset_status"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            legacy_asset_entry["blocker_codes"],
        )
        self.assertIn(
            "legacy_asset_manifest_not_migrated",
            legacy_asset_entry["blocker_codes"],
        )
        self.assertEqual(
            "no_task_assets_declared",
            asset_free_entry["asset_status"],
        )
        self.assertNotIn(
            "legacy_asset_manifest_not_migrated",
            asset_free_entry["blocker_codes"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            asset_free_entry["blocker_codes"],
        )

    def test_all_78_answer_tasks_use_native_evaluation_protocols(self) -> None:
        """确认 78 个非 WebMall QA 均路由到已迁移的原生答案协议。

        输入参数：
            无；从 canonical task 与确定性支持清单联合筛选目标闭包。
        输出返回值：
            无；任务数、协议前缀和 blocker 必须准确反映迁移状态。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {
            entry["task_id"]: entry for entry in manifest["tasks"]
        }
        answer_task_ids = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if (
                task.get("task_type") == "QA"
                and task.get("task_source") != "WebMall"
            ):
                answer_task_ids.append(task["task_id"])

        self.assertEqual(78, len(answer_task_ids))
        for task_id in answer_task_ids:
            entry = entries[task_id]
            self.assertTrue(
                entry["evaluation_protocol"].startswith(
                    "paraguibench.answer."
                )
            )
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )

    def test_validator_rejects_manual_support_escalation(self) -> None:
        """确认未验证任务不能仅靠编辑清单被升级为 live。

        输入参数：
            无；测试在临时目录中篡改一个支持条目。
        输出返回值：
            无；断言 validator 按 canonical 确定性推导拒绝误标。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        target = next(
            entry
            for entry in manifest["tasks"]
            if entry["task_id"]
            == "InformationRetrieval-FileSearch-Readonly-002"
        )
        target["support_status"] = "live_validated"
        target["support_reason_code"] = "live_validation_passed"
        target["blocker_codes"] = []

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "tampered.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            result = self.support_tool.validate_runtime_support_manifest(
                REPO_ROOT,
                manifest_path,
            )

        self.assertFalse(result.ok)
        self.assertTrue(
            any("任务状态偏离确定性推导" in error for error in result.errors)
        )


if __name__ == "__main__":
    unittest.main()
