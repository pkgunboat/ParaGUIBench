"""release-v1 canonical benchmark 的回归测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "validate_release.py"
MANIFEST_PATH = REPO_ROOT / "benchmark" / "manifests" / "release-v1.json"


def _load_validator() -> ModuleType:
    """从独立脚本路径加载发布校验模块。

    输入参数：
        无；脚本位置由仓库根目录推导。
    输出返回值：
        已执行并可调用的发布校验模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_release_validator",
        VALIDATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 release-v1 校验脚本")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseV1Test(unittest.TestCase):
    """验证正式任务数量、唯一性与清单引用完整性。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载一次校验模块，供本测试类复用。

        输入参数：
            无。
        输出返回值：
            无；结果写入测试类属性 ``validator``。
        """

        cls.validator = _load_validator()

    def test_release_validator_accepts_canonical_dataset(self) -> None:
        """确认完整 release-v1 数据集通过统一校验。

        输入参数：
            无。
        输出返回值：
            无；断言失败时由 unittest 报告差异。
        """

        result = self.validator.validate_release(REPO_ROOT, MANIFEST_PATH)
        self.assertEqual([], result.errors)
        self.assertEqual(233, result.task_count)

    def test_manifest_references_every_unique_task_id(self) -> None:
        """确认清单与磁盘任务形成一一对应关系。

        输入参数：
            无。
        输出返回值：
            无；断言失败时由 unittest 报告差异。
        """

        with MANIFEST_PATH.open("r", encoding="utf-8") as file:
            manifest = json.load(file)

        task_ids: list[str] = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            with task_path.open("r", encoding="utf-8") as file:
                task_ids.append(json.load(file)["task_id"])

        manifest_task_ids = [entry["task_id"] for entry in manifest["tasks"]]
        self.assertEqual(233, len(task_ids))
        self.assertEqual(len(task_ids), len(set(task_ids)))
        self.assertEqual(set(task_ids), set(manifest_task_ids))
        self.assertEqual(
            set(task_ids),
            set(manifest["task_id_mapping"].values()),
        )

    def test_validator_rejects_incomplete_manifest(self) -> None:
        """确认清单遗漏任务时校验器会明确失败。

        输入参数：
            无；测试在临时目录中构造缺少一个引用的清单。
        输出返回值：
            无；断言失败时由 unittest 报告差异。
        """

        with MANIFEST_PATH.open("r", encoding="utf-8") as file:
            incomplete_manifest = json.load(file)
        incomplete_manifest["tasks"].pop()

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_manifest = Path(temporary_directory) / "release-v1.json"
            with temporary_manifest.open("w", encoding="utf-8") as file:
                json.dump(incomplete_manifest, file)
            result = self.validator.validate_release(
                REPO_ROOT,
                temporary_manifest,
            )

        self.assertFalse(result.ok)
        self.assertTrue(
            any("清单任务条目应为 233" in error for error in result.errors)
        )
        self.assertTrue(
            any("清单遗漏 1 个 canonical task" in error for error in result.errors)
        )


if __name__ == "__main__":
    unittest.main()
