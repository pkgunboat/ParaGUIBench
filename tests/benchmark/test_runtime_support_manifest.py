"""runtime-support-v1 预览支持清单的行为回归测试。"""

from __future__ import annotations

import importlib.util
from collections import Counter
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import ModuleType

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
    ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
    ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
    inspect_artifact_family_task_prepare_capability,
)
from paraguibench.runtime.osworld_environment import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_TOOL_PATH = REPO_ROOT / "scripts" / "benchmark" / "runtime_support_manifest.py"
SUPPORT_MANIFEST_PATH = (
    REPO_ROOT / "benchmark" / "manifests" / "runtime-support-v1.json"
)
SUPPORT_SCHEMA_PATH = (
    REPO_ROOT / "benchmark" / "schemas" / "runtime-support-v1.schema.json"
)
VALIDATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "validate_runtime_support.py"
GENERATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "generate_runtime_support.py"
GLOBAL_IMAGE_BLOCKER = "osworld_vm_image_materialization_unverified"
SETTINGS_TASK_REFERENCE = "benchmark/tasks/Operation-FileOperate-Settings-001.json"
SETTINGS_INPUT_MANIFEST_REFERENCE = (
    "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
)
SETTINGS_GOLD_MANIFEST_REFERENCE = (
    "benchmark/gold/manifests/Operation-FileOperate-Settings-001.json"
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


def _copy_settings_contract_to_isolated_repo(repo_root: Path) -> dict[str, object]:
    """把 Settings-001 反向绑定的最小真实文件集复制到隔离仓库。

    输入参数：repo_root 为已存在的临时仓库根目录。
    输出返回值：返回 canonical Settings task JSON object；隔离根内
        只物化该 task 引用的 strict input manifest 和 v2 gold manifest。
    """

    for relative_reference in (
        SETTINGS_INPUT_MANIFEST_REFERENCE,
        SETTINGS_GOLD_MANIFEST_REFERENCE,
    ):
        source = REPO_ROOT / relative_reference
        target = repo_root / relative_reference
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return json.loads((REPO_ROOT / SETTINGS_TASK_REFERENCE).read_text(encoding="utf-8"))


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

    def test_task_entry_projection_requires_explicit_image_readiness(self) -> None:
        """确认私有 task 投影不能因遗漏镜像门禁而 fail-open。

        输入参数：
            无；检查 runtime-support 生成器的窄内部函数签名。
        输出返回值：
            无；``image_live_run_ready`` 必须由已读取正式
            manifest 的调用方显式传入，不允许默认为 True。
        """

        parameter = inspect.signature(self.support_tool._build_task_entry).parameters[
            "image_live_run_ready"
        ]

        self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_legacy_unversioned_gate_is_not_marked_live_validated(self) -> None:
        """确认旧的无版本纵向切片不能充当当前 live 证据。

        输入参数：
            无。
        输出返回值：
            无；断言 canonical 状态与 runtime 支持状态彼此独立。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = manifest["tasks"]
        live_entries = [
            entry for entry in entries if entry["support_status"] == "live_validated"
        ]

        self.assertEqual(233, len(entries))
        self.assertTrue(
            all(entry["canonical_status"] == "published" for entry in entries)
        )
        self.assertEqual([], live_entries)
        reference_entry = next(
            entry
            for entry in entries
            if entry["task_id"] == "InformationRetrieval-FileSearch-Readonly-001"
        )
        self.assertEqual(
            ["versioned_live_validation_not_completed"],
            reference_entry["blocker_codes"],
        )
        self.assertEqual(
            "live_validation_pending",
            reference_entry["support_reason_code"],
        )

    def test_verified_osworld_image_clears_only_the_global_image_gate(self) -> None:
        """确认可重现物化证据只清除全局镜像门禁。

        输入参数：
            无；从 canonical release 与正式 image manifest 重新生成支持清单。
        输出返回值：
            无；233 个任务均不再包含全局镜像 blocker，但仍全部
            ``blocked``、0 项 ``live_validated``；233 项都只剩显式
            实机复验 blocker。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = manifest["tasks"]

        self.assertEqual(233, len(entries))
        self.assertTrue(
            all(GLOBAL_IMAGE_BLOCKER not in entry["blocker_codes"] for entry in entries)
        )
        self.assertEqual(
            {"blocked": 233},
            dict(Counter(entry["support_status"] for entry in entries)),
        )
        self.assertEqual(
            {"live_validation_pending": 233},
            dict(Counter(entry["support_reason_code"] for entry in entries)),
        )
        self.assertEqual(
            {"local_components_incomplete": 0, "local_ready": 233},
            manifest["local_readiness_status_counts"],
        )

    def test_local_readiness_is_independent_from_live_validation(self) -> None:
        """本地组件就绪与真实环境验证必须使用两个独立投影。

        输入参数：
            无；从 canonical release 与全部正式 blocker 重新生成清单。
        输出返回值：
            无；精确断言 233 项 local-ready，同时所有任务仍然
            遵循正式 ``support_status`` 门禁。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = manifest["tasks"]
        expected_incomplete_task_ids: set[str] = set()

        self.assertEqual(
            {"local_components_incomplete": 0, "local_ready": 233},
            manifest["local_readiness_status_counts"],
        )
        self.assertEqual(
            expected_incomplete_task_ids,
            {
                entry["task_id"]
                for entry in entries
                if entry["local_readiness_status"] == "local_components_incomplete"
            },
        )
        self.assertTrue(
            all(
                entry["local_readiness_status"]
                == (
                    "local_components_incomplete"
                    if entry["task_id"] in expected_incomplete_task_ids
                    else "local_ready"
                )
                for entry in entries
            )
        )
        self.assertTrue(all(entry["support_status"] == "blocked" for entry in entries))

    def test_validator_reports_both_local_and_live_status_counts(self) -> None:
        """独立 validator 结果必须同时提供本地与正式 live 计数。

        输入参数：
            无；在临时文件中写入当前确定性生成结果。
        输出返回值：
            无；断言 validator 安全汇总 233/0 本地就绪度与
            233/0 正式支持状态，不依赖站点二次推导。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "runtime-support-v1.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            result = self.support_tool.validate_runtime_support_manifest(
                REPO_ROOT,
                manifest_path,
            )

        self.assertEqual([], result.errors)
        self.assertEqual({"local_ready": 233}, result.local_readiness_status_counts)
        self.assertEqual({"blocked": 233}, result.status_counts)

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
            {"blocked": 233},
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
            "runtime-support-v1 valid: tasks=233; local_readiness: "
            "local_ready=233; "
            "support_status: blocked=233\n",
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
        """确认已迁 QA 的固定资产与 evaluator blocker 均准确推导。

        输入参数：
            无。
        输出返回值：
            无；九个 Readonly 任务已移除 legacy asset 与全局镜像
            blocker，只保留版本化 live 门禁，不被误标为 live validated。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        asset_free_entry = entries[
            "InformationRetrieval-WebSearch-ConditionalSearch-001"
        ]

        migrated_task_ids = {
            "InformationRetrieval-FileSearch-Readonly-002",
            "InformationRetrieval-FileSearch-Readonly-003",
            "InformationRetrieval-FileSearch-ReadonlyPPT-001",
            "InformationRetrieval-FileSearch-ReadonlyPPT-004",
            "InformationRetrieval-FileSearch-ReadonlyPPT-005",
            "InformationRetrieval-FileSearch-ReadonlyWord-001",
            "InformationRetrieval-FileSearch-ReadonlyWord-002",
            "InformationRetrieval-FileSearch-ReadonlyWord-003",
            "InformationRetrieval-FileSearch-ReadonlyWord-004",
        }
        for task_id in migrated_task_ids:
            with self.subTest(task_id=task_id):
                entry = entries[task_id]
                self.assertEqual(
                    "pinned_download_manifest",
                    entry["asset_status"],
                )
                self.assertEqual("blocked", entry["support_status"])
                self.assertEqual(
                    "live_validation_pending",
                    entry["support_reason_code"],
                )
                self.assertEqual(
                    ["versioned_live_validation_not_completed"],
                    entry["blocker_codes"],
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
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        answer_task_ids = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if task.get("task_type") == "QA" and task.get("task_source") != "WebMall":
                answer_task_ids.append(task["task_id"])

        self.assertEqual(78, len(answer_task_ids))
        for task_id in answer_task_ids:
            entry = entries[task_id]
            self.assertTrue(
                entry["evaluation_protocol"].startswith("paraguibench.answer.")
            )
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )

    def test_checkout_and_end_to_end_use_distinct_closed_world_protocols(
        self,
    ) -> None:
        """确认 Checkout 与 EndToEnd 在镜像已验证后仍保留 live 门禁。

        输入参数：
            无；联合读取 canonical Checkout/EndToEnd 任务和确定性支持清单。
        输出返回值：
            无；8 个 Checkout 使用订单闭集，8 个 EndToEnd 使用报告
            URL AND 订单闭集；已接通组件不再误报，两组仍因
            带版本向量的真实运行未完成而阻塞。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        checkout_entries = [
            entry
            for entry in manifest["tasks"]
            if entry["task_id"].startswith(
                (
                    "Operation-OnlineShopping-Checkout-",
                    "Operation-OnlineShopping-EndToEnd-",
                )
            )
        ]

        self.assertEqual(16, len(checkout_entries))
        protocol_counts: dict[str, int] = {}
        for entry in checkout_entries:
            protocol = entry["evaluation_protocol"]
            protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )
            self.assertEqual(
                ["versioned_live_validation_not_completed"],
                entry["blocker_codes"],
            )
        self.assertEqual(
            {
                "paraguibench.webmall.checkout.closed-world.v2": 8,
                "paraguibench.webmall.find-and-order.closed-world.v2": 8,
            },
            protocol_counts,
        )

    def test_all_67_webmall_string_tasks_use_url_multiset_protocol(self) -> None:
        """确认原 string URL evaluator 闭集在镜像验证后保留 live 门禁。

        输入参数：
            无；联合 canonical WebMall QA 元数据与确定性清单。
        输出返回值：
            无；精确 67 条 string 任务使用 URL-multiset v1，
            不再有 legacy 或全局镜像 blocker，但 live 门禁仍保留。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        target_task_ids: list[str] = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if (
                task.get("task_type") == "QA"
                and task.get("task_source") == "WebMall"
                and task.get("evaluator_path") == "evaluators/string_url_evaluator.py"
            ):
                target_task_ids.append(task["task_id"])

        self.assertEqual(67, len(target_task_ids))
        for task_id in target_task_ids:
            entry = entries[task_id]
            self.assertEqual(
                "paraguibench.webmall.url-multiset.v1",
                entry["evaluation_protocol"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            self.assertEqual(
                ["versioned_live_validation_not_completed"],
                entry["blocker_codes"],
            )

    def test_all_eight_webmall_cart_tasks_are_native_but_not_live_ready(
        self,
    ) -> None:
        """确认 8 个 Cart 任务已完成评价器迁移但未越过真机门禁。

        输入参数：
            无；联合读取 canonical Cart 闭集与确定性 runtime-support 清单。
        输出返回值：
            无；8 项必须使用原生闭集协议、移除 legacy evaluator blocker，
            同时保留 Cart reader reference 与版本化真实运行门禁。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        task_ids: list[str] = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if (
                task.get("task_source") == "WebMall"
                and task.get("answer_type") == "cart"
                and task.get("evaluator_path") == "evaluators/cart_evaluator.py"
            ):
                task_ids.append(task["task_id"])

        self.assertEqual(8, len(task_ids))
        for task_id in task_ids:
            entry = entries[task_id]
            self.assertEqual(
                "paraguibench.webmall.cart.closed-world.v1",
                entry["evaluation_protocol"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )
            self.assertEqual(
                [
                    "webmall_cart_reader_reference_live_validation_not_completed",
                    "versioned_live_validation_not_completed",
                ],
                entry["blocker_codes"],
            )

    def test_webmall_url_and_cart_completion_preserves_other_158_task_semantics(
        self,
    ) -> None:
        """确认 URL/Cart 之外 158 项只发生授权的双层状态更新。

        输入参数：
            无；从确定性生成公共接口获取完整支持清单。
        输出返回值：
            无；断言排除 67 个 URL 与 8 个 Cart 原生任务后的稳定投影
            数量与已包含四项 pipeline-implicit、15 项 artifact-state、
            ReadonlyWord-003 及 32 项 Operation 资产接线的基线一致；
            本轮另外闭合 Settings-001 严格 derived-gold 语义合同并更新其
            ``local_readiness_status``；PPT-003 历史 receipt 不再清除
            普通 runtime-support 的 pipeline-live；其余语义无漂移。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        unchanged_entries = [
            entry
            for entry in manifest["tasks"]
            if entry["evaluation_protocol"]
            not in {
                "paraguibench.webmall.url-multiset.v1",
                "paraguibench.webmall.cart.closed-world.v1",
            }
        ]
        stable_projection = json.dumps(
            unchanged_entries,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertEqual(158, len(unchanged_entries))
        self.assertEqual(
            "c7045cdf742e8ff69a6606cfce4a928dba7d55dd5d69a1bdbfc7fc29bd883611",
            hashlib.sha256(stable_projection).hexdigest(),
        )

    def test_two_explicit_osworld_chrome_states_use_native_protocols(self) -> None:
        """确认 profile 与 active-tab 不再被混作一个 legacy/bookmark 协议。

        输入参数：
            无；读取两个带显式 ``evaluation_mode`` 的 canonical task。
        输出返回值：
            无；二者使用不同原生协议、移除 legacy evaluator blocker，
            生产证据链接通后二者都只保留版本化 live 门禁。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        profile = entries["Operation-WebOperate-Settings-001"]
        active_tab = entries["Operation-WebOperate-WebNavigate-009"]

        self.assertEqual(
            "paraguibench.osworld.chrome-profile-name.v1",
            profile["evaluation_protocol"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            profile["blocker_codes"],
        )
        self.assertEqual(
            ["versioned_live_validation_not_completed"],
            profile["blocker_codes"],
        )

        self.assertEqual(
            "paraguibench.osworld.google-shopping-active-tab.v1",
            active_tab["evaluation_protocol"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            active_tab["blocker_codes"],
        )
        self.assertEqual(
            ["versioned_live_validation_not_completed"],
            active_tab["blocker_codes"],
        )
        self.assertEqual("blocked", profile["support_status"])
        self.assertEqual("blocked", active_tab["support_status"])

    def test_all_eleven_bookmark_tasks_use_native_protocol_with_exact_blockers(
        self,
    ) -> None:
        """确认 11 个 Bookmark 任务均已接入原生协议与启动上下文。

        输入参数：
            无；从正式 task evaluator 路径筛选闭集并读取确定性清单。
        输出返回值：
            无；全部任务使用原生协议，Settings-003 使用 pinned
            PDF 与版本化 Chrome start-context，11 项均等待版本化 live。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        task_ids: list[str] = []
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if task.get("evaluator_path") == "eval/webnavigate_bookmark_evaluator.py":
                task_ids.append(task["task_id"])

        self.assertEqual(11, len(task_ids))
        settings_id = "Operation-WebOperate-Settings-003"
        for task_id in task_ids:
            entry = entries[task_id]
            self.assertEqual(
                "paraguibench.osworld.chrome-bookmarks.v1",
                entry["evaluation_protocol"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            self.assertEqual(
                ["versioned_live_validation_not_completed"],
                entry["blocker_codes"],
            )
            if task_id == settings_id:
                self.assertEqual(
                    "pinned_download_manifest",
                    entry["asset_status"],
                )

    def test_all_osworld_artifact_slices_declare_native_protocol_fail_closed(
        self,
    ) -> None:
        """确认 15 项 artifact evidence 精确闭集全部声明原生协议。

        输入参数：
            无；从 canonical OSWorld artifact task 闭集和确定性
            runtime-support 清单联合验证。
        输出返回值：
            无；15 项均移除 legacy evaluator blocker 但仍保持
            ``blocked``；最后 13 项精确保留 input/getter/gold/setup/live
            门禁，011/012/013 只移除已查明的 start-context blocker；10 项
            non-none finalize 已接线；Settings-001 的私有派生 gold
            已闭合本地语义，但仍保留真实环境门禁。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        artifact_task_ids: list[str] = []
        artifact_tasks_by_id: dict[str, dict[str, object]] = {}
        for task_path in sorted((REPO_ROOT / "benchmark" / "tasks").glob("*.json")):
            task = json.loads(task_path.read_text(encoding="utf-8"))
            evaluator_path = task.get("evaluator_path")
            if (
                task.get("task_type") == "OSWorld脚本"
                and isinstance(evaluator_path, str)
                and evaluator_path.startswith("eval/osworld_scripts/")
            ):
                artifact_task_ids.append(task["task_id"])
                artifact_tasks_by_id[task["task_id"]] = task

        self.assertEqual(15, len(artifact_task_ids))
        image_task_id = "Operation-FileOperate-BatchOperation-001"
        image_task = entries[image_task_id]
        self.assertIn(image_task_id, artifact_task_ids)
        self.assertEqual(
            "paraguibench.osworld.artifact-state.v1",
            image_task["evaluation_protocol"],
        )
        self.assertEqual("blocked", image_task["support_status"])
        self.assertEqual(
            "live_validation_pending",
            image_task["support_reason_code"],
        )
        self.assertEqual(
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            image_task["blocker_codes"],
        )
        self.assertEqual("pinned_download_manifest", image_task["asset_status"])
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            image_task["blocker_codes"],
        )

        bibtex_task_id = "Operation-FileOperate-CombinationDocs-015"
        bibtex_task = entries[bibtex_task_id]
        self.assertIn(bibtex_task_id, artifact_task_ids)
        self.assertEqual(
            "paraguibench.osworld.artifact-state.v1",
            bibtex_task["evaluation_protocol"],
        )
        self.assertEqual("pinned_download_manifest", bibtex_task["asset_status"])
        self.assertEqual("blocked", bibtex_task["support_status"])
        self.assertEqual(
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            bibtex_task["blocker_codes"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            bibtex_task["blocker_codes"],
        )

        remaining_task_ids = [
            task_id
            for task_id in artifact_task_ids
            if task_id not in {image_task_id, bibtex_task_id}
        ]
        self.assertEqual(13, len(remaining_task_ids))
        common_runtime_blockers = [
            "osworld_artifact_getter_live_validation_not_completed",
            "osworld_artifact_gold_live_validation_not_completed",
        ]
        finalize_action_task_ids = {
            task_id
            for task_id, prepare_spec in (ARTIFACT_FAMILY_TASK_PREPARE_SPECS.items())
            if prepare_spec.finalize_action_id != "none"
        }
        self.assertEqual(10, len(finalize_action_task_ids))
        self.assertEqual(
            finalize_action_task_ids,
            set(OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS),
        )
        self.assertTrue(finalize_action_task_ids < set(remaining_task_ids))
        capability_by_task = {
            task_id: inspect_artifact_family_task_prepare_capability(
                repo_root=REPO_ROOT,
                task=artifact_tasks_by_id[task_id],
            )
            for task_id in remaining_task_ids
        }
        self.assertTrue(all(capability_by_task.values()))
        capability_blocker_projection = (
            (
                ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
                "osworld_source_start_context_ambiguous",
            ),
            (
                ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
                "osworld_artifact_input_path_inferred",
            ),
            (
                ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
                "osworld_artifact_input_license_unverified",
            ),
        )
        for task_id in remaining_task_ids:
            entry = entries[task_id]
            capability = capability_by_task[task_id]
            self.assertIsNotNone(capability)
            expected_blockers = []
            if entry["asset_status"] == "legacy_remote_reference":
                expected_blockers.append("legacy_asset_manifest_not_migrated")
            expected_blockers.extend(common_runtime_blockers)
            expected_blockers.append("osworld_task_setup_live_validation_not_completed")
            for internal_code, public_code in capability_blocker_projection:
                if internal_code in capability.blocker_ids:
                    expected_blockers.append(public_code)
            expected_blockers.append("versioned_live_validation_not_completed")
            self.assertEqual(
                "paraguibench.osworld.artifact-state.v1",
                entry["evaluation_protocol"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            self.assertEqual(expected_blockers, entry["blocker_codes"])
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )
            self.assertNotIn(
                "osworld_artifact_finalize_not_migrated",
                entry["blocker_codes"],
            )

        for task_id in {
            "Operation-FileOperate-CombinationDocs-011",
            "Operation-FileOperate-CombinationDocs-012",
            "Operation-FileOperate-CombinationDocs-013",
        }:
            entry = entries[task_id]
            self.assertEqual(
                [
                    "osworld_artifact_getter_live_validation_not_completed",
                    "osworld_artifact_gold_live_validation_not_completed",
                    "osworld_task_setup_live_validation_not_completed",
                    "versioned_live_validation_not_completed",
                ],
                entry["blocker_codes"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )

        for internal_code, _public_code, expected_count in (
            (
                ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
                "osworld_source_start_context_ambiguous",
                0,
            ),
            (
                ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
                "osworld_artifact_input_path_inferred",
                0,
            ),
            (
                ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
                "osworld_artifact_input_license_unverified",
                0,
            ),
        ):
            self.assertEqual(
                expected_count,
                sum(
                    internal_code in capability.blocker_ids
                    for capability in capability_by_task.values()
                ),
            )

        self.assertEqual(
            233,
            sum(
                entry["evaluation_protocol"].startswith("paraguibench.")
                for entry in entries.values()
            ),
        )
        self.assertEqual(
            0,
            sum(
                "legacy_evaluator_not_migrated" in entry["blocker_codes"]
                for entry in entries.values()
            ),
        )

    def test_settings_local_projection_requires_strict_derived_gold_binding(
        self,
    ) -> None:
        """Settings 本地就绪必须实际验证 canonical v2 gold 合同。

        输入参数：无；先读取 canonical Settings task，再让严格 gold loader
            模拟 manifest 完整性失败。
        输出返回值：生成器在状态投影前固定失败关闭，不能只因删除旧
            conflict blocker 就把任务标成 ``local_ready``。
        """

        task = json.loads(
            (
                REPO_ROOT / "benchmark/tasks/Operation-FileOperate-Settings-001.json"
            ).read_text(encoding="utf-8")
        )
        task["gold_manifest"] = (
            "benchmark/gold/manifests/Operation-FileOperate-Settings-001.json"
        )
        with patch.object(
            self.support_tool,
            "load_gold_asset_manifest",
            side_effect=self.support_tool.GoldManifestError,
        ):
            with self.assertRaises(self.support_tool.RuntimeSupportError):
                self.support_tool._derive_osworld_artifact_runtime_blockers(
                    REPO_ROOT,
                    task,
                )

    def test_settings_local_projection_rejects_input_manifest_byte_drift(
        self,
    ) -> None:
        """Settings input manifest 的原始字节身份不得被语义解析吞掉。

        输入参数：无；在隔离仓库复制真实 task/input/gold
            合同，再仅向 strict input manifest 末尾追加一个空格。
        输出返回值：Settings 专用 runtime 合同边界必须以
            ``RuntimeSupportError`` 失败关闭，不得继续投影 local-ready。
        """

        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary_directory:
            isolated_root = Path(temporary_directory)
            task = _copy_settings_contract_to_isolated_repo(isolated_root)
            manifest_path = isolated_root / SETTINGS_INPUT_MANIFEST_REFERENCE
            manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

            with self.assertRaises(self.support_tool.RuntimeSupportError):
                self.support_tool._validate_settings_derived_gold_contract(
                    isolated_root,
                    task,
                )

    def test_settings_local_projection_rejects_source_video_semantic_drift(
        self,
    ) -> None:
        """Settings v2 source video 必须反向绑定 input manifest 唯一条目。

        输入参数：无；在隔离仓库逐项篡改 source MP4
            的 path、size、SHA-256 或 media_type，并把 v2 摘要
            比较固定为匹配，以隔离语义反向绑定门禁。
        输出返回值：每一种语义漂移均以
            ``RuntimeSupportError`` 失败关闭。
        """

        mutations: tuple[tuple[str, object], ...] = (
            ("path", "alternate.mp4"),
            ("size", 9_362_832),
            ("sha256", "0" * 64),
            ("media_type", "application/octet-stream"),
        )
        for field_name, replacement in mutations:
            with self.subTest(field_name=field_name):
                with tempfile.TemporaryDirectory(
                    dir=Path(tempfile.gettempdir()).resolve(),
                ) as temporary_directory:
                    isolated_root = Path(temporary_directory)
                    task = _copy_settings_contract_to_isolated_repo(isolated_root)
                    input_path = isolated_root / SETTINGS_INPUT_MANIFEST_REFERENCE
                    input_document = json.loads(input_path.read_text(encoding="utf-8"))
                    source_video = next(
                        item
                        for item in input_document["files"]
                        if item["path"] == "landscape.mp4"
                    )
                    source_video[field_name] = replacement
                    input_payload = (
                        json.dumps(input_document, ensure_ascii=False, indent=2) + "\n"
                    ).encode("utf-8")
                    input_path.write_bytes(input_payload)

                    gold_path = isolated_root / SETTINGS_GOLD_MANIFEST_REFERENCE
                    gold_document = json.loads(gold_path.read_text(encoding="utf-8"))
                    expected_digest = gold_document["derived_from_input"][
                        "asset_manifest_sha256"
                    ]

                    with patch.object(
                        self.support_tool.hashlib,
                        "sha256",
                    ) as digest_constructor:
                        digest_constructor.return_value.hexdigest.return_value = (
                            expected_digest
                        )
                        with self.assertRaises(self.support_tool.RuntimeSupportError):
                            self.support_tool._validate_settings_derived_gold_contract(
                                isolated_root,
                                task,
                            )

    def test_settings_local_projection_rejects_input_asset_set_drift(self) -> None:
        """Settings input manifest 的 asset_set_id 必须反向绑定 v2 gold。

        输入参数：无；在隔离仓库中篡改 input manifest
            ``asset_set_id``，同时把摘要比较固定为匹配。
        输出返回值：Settings 专用合同边界以
            ``RuntimeSupportError`` 失败关闭。
        """

        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary_directory:
            isolated_root = Path(temporary_directory)
            task = _copy_settings_contract_to_isolated_repo(isolated_root)
            input_path = isolated_root / SETTINGS_INPUT_MANIFEST_REFERENCE
            input_document = json.loads(input_path.read_text(encoding="utf-8"))
            input_document["asset_set_id"] = "Operation-FileOperate-Settings-002"
            input_path.write_text(
                json.dumps(input_document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            gold_document = json.loads(
                (isolated_root / SETTINGS_GOLD_MANIFEST_REFERENCE).read_text(
                    encoding="utf-8"
                )
            )
            expected_digest = gold_document["derived_from_input"][
                "asset_manifest_sha256"
            ]

            with patch.object(
                self.support_tool.hashlib,
                "sha256",
            ) as digest_constructor:
                digest_constructor.return_value.hexdigest.return_value = expected_digest
                with self.assertRaises(self.support_tool.RuntimeSupportError):
                    self.support_tool._validate_settings_derived_gold_contract(
                        isolated_root,
                        task,
                    )

    def test_first_sheet_artifact_slice_uses_native_protocol_fail_closed(
        self,
    ) -> None:
        """确认 CombinationDocs-010 首张工作表切片原生但仍阻塞。

        输入参数：
            无；从 canonical release 构造确定性 runtime-support 清单。
        输出返回值：
            无；评价协议已升级为 artifact-state 且 input/gold
            manifest 已固定，仅保留 getter、gold、setup 与带版本
            live 证据门禁。
        """

        entries = {
            entry["task_id"]: entry
            for entry in self.support_tool.build_runtime_support_manifest(REPO_ROOT)[
                "tasks"
            ]
        }
        entry = entries["Operation-FileOperate-CombinationDocs-010"]

        self.assertEqual(
            "paraguibench.osworld.artifact-state.v1",
            entry["evaluation_protocol"],
        )
        self.assertEqual("pinned_download_manifest", entry["asset_status"])
        self.assertEqual("blocked", entry["support_status"])
        self.assertEqual(
            "live_validation_pending",
            entry["support_reason_code"],
        )
        self.assertEqual(
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            entry["blocker_codes"],
        )
        self.assertNotIn(
            "legacy_evaluator_not_migrated",
            entry["blocker_codes"],
        )
        self.assertNotIn(
            "legacy_asset_manifest_not_migrated",
            entry["blocker_codes"],
        )
        self.assertNotIn(
            "osworld_artifact_input_path_inferred",
            entry["blocker_codes"],
        )
        self.assertNotIn(
            "osworld_artifact_input_license_unverified",
            entry["blocker_codes"],
        )

    def test_batch003_strict_assets_clear_only_verified_input_blockers(
        self,
    ) -> None:
        """确认 Batch003 晋升 input/gold manifest 后仍严格保留 live 门禁。

        输入参数：
            无；从 canonical release 与 task-prepare capability 生成清单。
        输出返回值：
            无；asset 状态升级为 pinned，仅移除 legacy/path/license 类
            blocker；getter、gold、setup 和版本化 live blocker 不得被
            资产迁移误清除。
        """

        task_id = "Operation-FileOperate-BatchOperation-003"
        entries = {
            entry["task_id"]: entry
            for entry in self.support_tool.build_runtime_support_manifest(REPO_ROOT)[
                "tasks"
            ]
        }
        entry = entries[task_id]

        self.assertEqual("pinned_download_manifest", entry["asset_status"])
        self.assertEqual("blocked", entry["support_status"])
        self.assertEqual(
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            entry["blocker_codes"],
        )
        for cleared_code in (
            "legacy_asset_manifest_not_migrated",
            "osworld_artifact_input_path_inferred",
            "osworld_artifact_input_license_unverified",
        ):
            self.assertNotIn(cleared_code, entry["blocker_codes"])

    def test_osworld_artifact_binding_rejects_unknown_or_drifted_metadata(
        self,
    ) -> None:
        """确认 artifact 原生协议不会按路径前缀泛化提升。

        输入参数：
            无；读取 CombinationDocs-010 canonical 任务，分别
            修改 task ID 和已绑定的 task UID。
        输出返回值：
            无；未知任务或任一身份字段漂移都回退到
            ``legacy.osworld.state.v1``，不会误报原生支持。
        """

        task_path = (
            REPO_ROOT / "benchmark/tasks/Operation-FileOperate-CombinationDocs-010.json"
        )
        canonical = json.loads(task_path.read_text(encoding="utf-8"))
        unknown = dict(canonical)
        unknown["task_id"] = "Operation-FileOperate-FutureArtifact-999"
        drifted = dict(canonical)
        drifted["task_uid"] = "00000000-0000-0000-0000-000000000000"

        for task in (unknown, drifted):
            self.assertEqual(
                "legacy.osworld.state.v1",
                self.support_tool._derive_evaluation_protocol(task),
            )

    def test_all_32_operation_rule_tasks_use_native_protocol_with_asset_gate(
        self,
    ) -> None:
        """确认只提升固定 32-task Operation 闭集并保留资产门禁。

        输入参数：
            无；从生成器固定 binding、canonical task 与确定性清单
            联合验证。
        输出返回值：
            无；32 项均使用原生协议，不再携带 legacy evaluator
            blocker；三十二项全部使用固定下载清单，仅保留镜像、
            任务专属语义与 versioned live 阻塞项；任意
            未绑定 eval_rules 任务仍保持 legacy。
        """

        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
        task_ids = self.support_tool.NATIVE_OPERATION_TASK_IDS
        pinned_asset_task_ids = {
            "Operation-FileOperate-BatchOperationExcel-001",
            "Operation-FileOperate-BatchOperationExcel-002",
            "Operation-FileOperate-BatchOperationExcel-003",
            "Operation-FileOperate-BatchOperationExcel-004",
            "Operation-FileOperate-BatchOperationExcel-005",
            "Operation-FileOperate-BatchOperationExcel-006",
            "Operation-FileOperate-BatchOperationExcel-007",
            "Operation-FileOperate-BatchOperationExcel-009",
            "Operation-FileOperate-BatchOperationPPT-001",
            "Operation-FileOperate-BatchOperationPPT-002",
            "Operation-FileOperate-BatchOperationWord-001",
            "Operation-FileOperate-BatchOperationWord-002",
            "Operation-FileOperate-BatchOperationWord-003",
            "Operation-FileOperate-BatchOperationWord-004",
            "Operation-FileOperate-BatchOperationWord-005",
            "Operation-FileOperate-BatchOperationWord-006",
            "Operation-FileOperate-BatchOperationWord-007",
            "Operation-FileOperate-BatchOperationWord-008",
            "Operation-FileOperate-BatchOperationWord-009",
            "Operation-FileOperate-BatchOperationWord-010",
            "Operation-FileOperate-BatchOperationWord-011",
            "Operation-FileOperate-BatchOperationWord-012",
            "Operation-FileOperate-CombinationDocs-001",
            "Operation-FileOperate-CombinationDocs-003",
            "Operation-FileOperate-CombinationDocs-005",
            "Operation-FileOperate-CombinationDocs-006",
            "Operation-FileOperate-CombinationDocs-007",
            "Operation-FileOperate-CombinationDocs-008",
            "Operation-FileOperate-SearchAndWrite-002",
            "Operation-FileOperate-SearchAndWrite-004",
            "Operation-FileOperate-SearchAndWrite-006",
            "Operation-FileOperate-SearchAndWrite-007",
        }

        self.assertEqual(32, len(task_ids))
        self.assertEqual(32, len(pinned_asset_task_ids))
        self.assertEqual(task_ids, pinned_asset_task_ids)
        for task_id in sorted(task_ids):
            entry = entries[task_id]
            self.assertEqual(
                "paraguibench.operation.eval-rules.v1",
                entry["evaluation_protocol"],
            )
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual(
                "live_validation_pending",
                entry["support_reason_code"],
            )
            if task_id in pinned_asset_task_ids:
                self.assertEqual("pinned_download_manifest", entry["asset_status"])
                expected_blockers = []
                if task_id in {
                    "Operation-FileOperate-BatchOperationWord-009",
                    "Operation-FileOperate-BatchOperationWord-010",
                }:
                    expected_blockers.append(
                        "operation_word009_010_writer_live_validation_not_completed"
                    )
                if task_id == "Operation-FileOperate-CombinationDocs-003":
                    expected_blockers.append(
                        "combinationdocs003_real_render_validation_not_completed"
                    )
                expected_blockers.append("versioned_live_validation_not_completed")
                self.assertEqual(
                    expected_blockers,
                    entry["blocker_codes"],
                )
        self.assertEqual(
            "legacy.operation.eval-rules.v1",
            self.support_tool._derive_evaluation_protocol(
                {
                    "task_id": "Operation-FileOperate-Future-999",
                    "task_tag": "FileOperate",
                    "evaluator_path": "",
                    "eval_rules": [{"check": "future_check"}],
                }
            ),
        )

    def test_schema_exposes_combinationdocs003_real_render_blocker(self) -> None:
        """验证 003 真实渲染门禁是严格公开枚举值。

        输入参数：
            无；读取正式 runtime-support JSON Schema。
        输出返回值：
            无；专属 blocker 必须在严格 enum 中，不得以
            未受约束字符串方式加入支持清单。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        blocker_codes = schema["$defs"]["taskSupport"]["properties"]["blocker_codes"][
            "items"
        ]["enum"]

        self.assertIn(
            "combinationdocs003_real_render_validation_not_completed",
            blocker_codes,
        )

    def test_schema_allows_native_artifact_protocol_and_specific_blockers(
        self,
    ) -> None:
        """确认公开 runtime-support schema 接受 artifact 声明闭环。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；断言 artifact-state 协议与 getter/gold/finalize/
            setup blocker 都在严格 enum 中；已解决的 Settings gold
            conflict 不得继续出现在公开词汇中。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_properties = schema["$defs"]["taskSupport"]["properties"]

        self.assertIn(
            "paraguibench.osworld.artifact-state.v1",
            task_properties["evaluation_protocol"]["enum"],
        )
        blocker_codes = task_properties["blocker_codes"]["items"]["enum"]
        for blocker_code in (
            "osworld_artifact_getter_live_validation_not_completed",
            "osworld_artifact_gold_live_validation_not_completed",
            "osworld_artifact_finalize_not_migrated",
            "osworld_task_setup_live_validation_not_completed",
            "osworld_source_start_context_ambiguous",
            "osworld_artifact_input_path_inferred",
            "osworld_artifact_input_license_unverified",
        ):
            self.assertIn(blocker_code, blocker_codes)
        self.assertNotIn(
            "osworld_artifact_settings_gold_conflict_unresolved",
            blocker_codes,
        )

    def test_pipeline_implicit_tasks_use_native_protocols_with_exact_blockers(
        self,
    ) -> None:
        """确认四项 implicit 任务升级协议但不误报 live-ready。

        输入参数：
            无；从 canonical release 构造确定性支持清单。
        输出返回值：
            无；四项必须分别使用原生协议并移除 legacy evaluator；
            四项已移除完成的 input/reference/parser 门禁；
            四项均保留 pipeline-live 与 versioned-live，历史 receipt
            不作为普通 runtime-support 门禁。
        """

        protocols = {
            "Operation-FileOperate-BatchOperationExcel-008": (
                "paraguibench.operation.xlsx.hide-na-rows.v1"
            ),
            "Operation-FileOperate-BatchOperationPPT-003": (
                "paraguibench.operation.image-classification.sha256.v1"
            ),
            "Operation-FileOperate-CombinationDocs-002": (
                "paraguibench.operation.cross-document-facts.v1"
            ),
            "Operation-FileOperate-SearchAndWrite-008": (
                "paraguibench.operation.searchwrite-xlsx.v1"
            ),
        }
        entries = {
            entry["task_id"]: entry
            for entry in self.support_tool.build_runtime_support_manifest(REPO_ROOT)[
                "tasks"
            ]
        }
        for task_id, protocol in protocols.items():
            entry = entries[task_id]
            expected_blockers = [
                "pipeline_implicit_live_validation_not_completed",
            ]
            self.assertEqual(
                "pinned_download_manifest",
                entry["asset_status"],
            )
            expected_blockers.append("versioned_live_validation_not_completed")
            self.assertEqual(protocol, entry["evaluation_protocol"])
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual("live_validation_pending", entry["support_reason_code"])
            self.assertEqual(expected_blockers, entry["blocker_codes"])
            self.assertNotIn(
                "legacy_evaluator_not_migrated",
                entry["blocker_codes"],
            )

    def test_ppt003_formal_runtime_only_waits_for_image_and_live_evidence(
        self,
    ) -> None:
        """确认 PPT-003 正式组件不再保留三项虚假 metadata/parser blocker。

        输入参数：
            无；直接把真实 canonical task 投影为 runtime-support 条目，避免
            当前串行派生阶段尚未更新的 release 清单影响组件行为测试。
        输出返回值：
            无；任务仍是 blocked，且只保留全局镜像、pipeline 首次实机
            复验和通用 versioned-live 三项门禁，不误报 live-ready。
        """

        task_id = "Operation-FileOperate-BatchOperationPPT-003"
        task = json.loads(
            (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )

        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=False,
        )

        self.assertEqual(
            "paraguibench.operation.image-classification.sha256.v1",
            entry["evaluation_protocol"],
        )
        self.assertEqual("pinned_download_manifest", entry["asset_status"])
        self.assertEqual("blocked", entry["support_status"])
        self.assertEqual(
            "runtime_components_incomplete",
            entry["support_reason_code"],
        )
        self.assertEqual(
            [
                GLOBAL_IMAGE_BLOCKER,
                "pipeline_implicit_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            entry["blocker_codes"],
        )

    def test_searchwrite_formal_runtime_only_waits_for_image_and_live_evidence(
        self,
    ) -> None:
        """确认 SearchWrite 正式本地 runtime 只等待镜像与 live 证据。

        输入参数：
            无；直接投影真实 canonical，不依赖待串行更新的
            release/runtime-support 派生文件。
        输出返回值：
            无；asset status 为正式 pinned manifest，input/gold/typed
            本地 blocker 全部消失，仅保留镜像、pipeline-live 与 versioned-live。
        """

        task_id = "Operation-FileOperate-SearchAndWrite-008"
        task = json.loads(
            (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )

        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=False,
        )

        self.assertEqual(
            "paraguibench.operation.searchwrite-xlsx.v1",
            entry["evaluation_protocol"],
        )
        self.assertEqual("pinned_download_manifest", entry["asset_status"])
        self.assertEqual("blocked", entry["support_status"])
        self.assertEqual(
            "runtime_components_incomplete",
            entry["support_reason_code"],
        )
        self.assertEqual(
            [
                GLOBAL_IMAGE_BLOCKER,
                "pipeline_implicit_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            entry["blocker_codes"],
        )

    def test_ppt003_component_completion_never_claims_live_support(self) -> None:
        """确认即使合成镜像 ready，PPT-003 仍需真实 pipeline 版本化复验。

        输入参数：
            无；直接投影真实 canonical，并仅把全局镜像状态设为 ready。
        输出返回值：
            无；support 仍为 blocked/live-validation-pending，不能把正式
            evaluator 组件完成错误升级成 ``live_validated``。
        """

        task_id = "Operation-FileOperate-BatchOperationPPT-003"
        task = json.loads(
            (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )

        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=True,
        )

        self.assertEqual("blocked", entry["support_status"])
        self.assertEqual(
            "live_validation_pending",
            entry["support_reason_code"],
        )
        self.assertEqual(
            [
                "pipeline_implicit_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            ],
            entry["blocker_codes"],
        )

    def test_schema_allows_pipeline_implicit_protocols_and_blockers(
        self,
    ) -> None:
        """确认四项原生协议与专属门禁都受 schema 严格枚举。

        输入参数：无；读取版本化 runtime-support schema。
        输出返回值：
            无；不依赖未受约束字符串扩展协议或 blocker。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        properties = schema["$defs"]["taskSupport"]["properties"]
        protocols = properties["evaluation_protocol"]["enum"]
        blockers = properties["blocker_codes"]["items"]["enum"]

        for protocol in (
            "paraguibench.operation.xlsx.hide-na-rows.v1",
            "paraguibench.operation.image-classification.sha256.v1",
            "paraguibench.operation.cross-document-facts.v1",
            "paraguibench.operation.searchwrite-xlsx.v1",
        ):
            self.assertIn(protocol, protocols)
        for blocker in (
            "pipeline_implicit_input_asset_metadata_unverified",
            "pipeline_implicit_gold_asset_metadata_unverified",
            "pipeline_implicit_typed_observation_parser_not_migrated",
            "pipeline_implicit_live_validation_not_completed",
            "pipeline_implicit_combination_gold_conflict_unresolved",
        ):
            self.assertIn(blocker, blockers)

    def test_native_operation_with_pinned_assets_only_waits_for_live_validation(
        self,
    ) -> None:
        """确认已验证的 Operation 固定资产只等待 live validation。

        输入参数：
            无；读取已完成 deterministic manifest 绑定的真实 Excel-001。
        输出返回值：
            无；原生 evaluator 与资产门禁均闭环后，必须只余
            ``versioned_live_validation_not_completed``。
        """

        task_id = "Operation-FileOperate-BatchOperationExcel-001"
        task = json.loads(
            (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )

        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=True,
        )

        self.assertEqual(
            "paraguibench.operation.eval-rules.v1",
            entry["evaluation_protocol"],
        )
        self.assertEqual("pinned_download_manifest", entry["asset_status"])
        self.assertEqual(
            "live_validation_pending",
            entry["support_reason_code"],
        )
        self.assertEqual(
            ["versioned_live_validation_not_completed"],
            entry["blocker_codes"],
        )

    def test_schema_allows_native_operation_protocol(self) -> None:
        """确认 runtime-support schema 严格枚举原生 Operation 协议。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；新协议必须存在于严格 enum，旧 legacy 值仍保留
            给未知未来任务 fail closed。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        protocols = schema["$defs"]["taskSupport"]["properties"]["evaluation_protocol"][
            "enum"
        ]

        self.assertIn("paraguibench.operation.eval-rules.v1", protocols)
        self.assertIn("legacy.operation.eval-rules.v1", protocols)

    def test_schema_allows_native_bookmark_protocol_and_start_context_blocker(
        self,
    ) -> None:
        """确认公开 schema 严格枚举 Bookmark 协议与 Settings blocker。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；原生协议及启动上下文未迁移 blocker 均为受控枚举值。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_properties = schema["$defs"]["taskSupport"]["properties"]

        self.assertIn(
            "paraguibench.osworld.chrome-bookmarks.v1",
            task_properties["evaluation_protocol"]["enum"],
        )
        self.assertIn(
            "osworld_bookmark_start_context_not_migrated",
            task_properties["blocker_codes"]["items"]["enum"],
        )

    def test_schema_uses_live_pending_reason_without_obsolete_webmall_codes(
        self,
    ) -> None:
        """确认 schema 表达 WebMall 只等待 live 验证的真实状态。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；断言 pending 原因可用，四个已完成组件的
            blocker code 不再属于当前 schema 词汇。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_properties = schema["$defs"]["taskSupport"]["properties"]
        reason_codes = task_properties["support_reason_code"]["enum"]
        blocker_codes = task_properties["blocker_codes"]["items"]["enum"]

        self.assertIn("live_validation_pending", reason_codes)
        for obsolete_code in (
            "webmall_privileged_order_source_not_integrated",
            "webmall_distributed_lease_not_integrated",
            "webmall_environment_manifest_not_integrated",
            "webmall_cli_runtime_binding_not_integrated",
        ):
            self.assertNotIn(obsolete_code, blocker_codes)
        self.assertIn(
            "osworld_artifact_gold_live_validation_not_completed",
            task_properties["blocker_codes"]["items"]["enum"],
        )
        self.assertIn(
            "osworld_task_setup_live_validation_not_completed",
            task_properties["blocker_codes"]["items"]["enum"],
        )

    def test_schema_requires_independent_local_readiness_projection(self) -> None:
        """schema 必须同时约束根计数与每任务的本地就绪度。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；根对象必须提供两类完整计数，任务条目必须只能
            使用 ``local_ready/local_components_incomplete``。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_support = schema["$defs"]["taskSupport"]
        root_counts = schema["properties"]["local_readiness_status_counts"]

        self.assertIn("local_readiness_status_counts", schema["required"])
        self.assertEqual(
            ["local_components_incomplete", "local_ready"],
            root_counts["required"],
        )
        self.assertFalse(root_counts["additionalProperties"])
        for status in root_counts["required"]:
            self.assertEqual(0, root_counts["properties"][status]["minimum"])
            self.assertEqual(233, root_counts["properties"][status]["maximum"])
        self.assertIn("local_readiness_status", task_support["required"])
        self.assertEqual(
            ["local_ready", "local_components_incomplete"],
            task_support["properties"]["local_readiness_status"]["enum"],
        )
        blocker_codes = task_support["properties"]["blocker_codes"]["items"]["enum"]
        self.assertIn(
            "operation_word009_010_writer_live_validation_not_completed",
            blocker_codes,
        )
        self.assertNotIn(
            "operation_word009_010_docx_text_fidelity_not_migrated",
            blocker_codes,
        )

    def test_schema_semantics_rejects_local_incomplete_with_only_live_blockers(
        self,
    ) -> None:
        """本地就绪状态不得脱离 blocker 分类被手工降级。

        输入参数：
            无；Settings-001 已只剩 live-only blocker，但篡改其
            ``local_readiness_status`` 为 incomplete。
        输出返回值：
            无；项目级 schema 语义校验必须以固定错误失败关闭。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        target = next(
            entry
            for entry in manifest["tasks"]
            if entry["task_id"] == "Operation-FileOperate-Settings-001"
        )
        target["local_readiness_status"] = "local_components_incomplete"

        errors = self.support_tool._validate_runtime_support_schema_instance(
            schema,
            manifest,
        )

        self.assertTrue(
            any("local-readiness-classification" in error for error in errors)
        )

    def test_schema_semantics_returns_errors_for_non_string_blocker(self) -> None:
        """恶意非字符串 blocker 必须失败关闭而不得使 validator 崩溃。

        输入参数：
            无；将一条任务的 blocker 篡改为不可哈希 JSON object。
        输出返回值：
            无；校验器必须返回结构化 schema 错误，不抛出 ``TypeError``。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        manifest = self.support_tool.build_runtime_support_manifest(REPO_ROOT)
        manifest["tasks"][0]["blocker_codes"] = [{}]

        errors = self.support_tool._validate_runtime_support_schema_instance(
            schema,
            manifest,
        )

        self.assertTrue(errors)
        self.assertTrue(any("enum" in error for error in errors))

    def test_unknown_blocker_is_local_components_incomplete(self) -> None:
        """未来新增 blocker 在未明确归类前必须默认为本地未闭合。

        输入参数：
            无；向窄分类接口传入一个当前不存在的 blocker code。
        输出返回值：
            无；未知值必须失败关闭为 ``local_components_incomplete``。
        """

        self.assertEqual(
            "local_components_incomplete",
            self.support_tool._derive_local_readiness_status(
                ["future_component_gate_not_classified"]
            ),
        )

    def test_schema_accepts_task_specific_live_only_entries_after_image_gate(
        self,
    ) -> None:
        """schema 必须接受镜像门禁清除后仍等待专属实机复验的条目。

        输入参数：
            无；对 Word-009 与 CombinationDocs-003 派生 image-ready、
            task-specific live blocker 仍存在的生成器可达状态。
        输出返回值：
            无；两条目必须为 local-ready/blocked/live-pending，且能通过
            taskSupport schema，不会在未来镜像验证后将清单卡死。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_schema = schema["$defs"]["taskSupport"]
        for task_id in (
            "Operation-FileOperate-BatchOperationWord-009",
            "Operation-FileOperate-CombinationDocs-003",
        ):
            task = json.loads(
                (REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            entry = self.support_tool._build_task_entry(
                REPO_ROOT,
                task,
                image_live_run_ready=True,
            )
            errors: list[str] = []
            self.support_tool._validate_project_schema_node(
                root_schema=schema,
                node_schema=task_schema,
                instance=entry,
                location="$task",
                errors=errors,
            )

            self.assertEqual("local_ready", entry["local_readiness_status"])
            self.assertEqual("blocked", entry["support_status"])
            self.assertEqual("live_validation_pending", entry["support_reason_code"])
            self.assertEqual([], errors)

    def test_schema_allows_native_webmall_url_multiset_protocol(self) -> None:
        """确认 runtime-support schema 使 URL 原生协议成为严格枚举值。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；新 URL-multiset v1 协议必须存在于严格 enum。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        protocols = schema["$defs"]["taskSupport"]["properties"]["evaluation_protocol"][
            "enum"
        ]

        self.assertIn(
            "paraguibench.webmall.url-multiset.v1",
            protocols,
        )

    def test_schema_allows_native_cart_protocol_and_reader_live_blocker(
        self,
    ) -> None:
        """确认 schema 严格枚举 Cart evaluator-ready 与 live gate。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；原生 Cart 协议与 reference reader 真机门禁均为受控值，
            legacy Cart 值仍用于未命中权威闭集的 fail-closed fallback。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        task_properties = schema["$defs"]["taskSupport"]["properties"]
        protocols = task_properties["evaluation_protocol"]["enum"]
        blocker_codes = task_properties["blocker_codes"]["items"]["enum"]

        self.assertIn(
            "paraguibench.webmall.cart.closed-world.v1",
            protocols,
        )
        self.assertIn("legacy.webmall.cart.v1", protocols)
        self.assertIn(
            "webmall_cart_reader_reference_live_validation_not_completed",
            blocker_codes,
        )

    def test_schema_enumerates_unverified_vm_image_blocker(self) -> None:
        """确认公开 schema 接受且只按固定码表达镜像来源链阻断。

        输入参数：
            无；读取版本化 runtime-support JSON Schema。
        输出返回值：
            无；全局镜像物化 blocker 必须进入严格枚举。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        blocker_codes = schema["$defs"]["taskSupport"]["properties"]["blocker_codes"][
            "items"
        ]["enum"]

        self.assertIn(
            "osworld_vm_image_materialization_unverified",
            blocker_codes,
        )

    def test_schema_keeps_cart_reader_and_versioned_live_gates_distinct(
        self,
    ) -> None:
        """确认 Cart pending 条目区分已完成和未完成的 component proof。

        输入参数：
            无；读取 runtime-support schema 的 pending 条件分支。
        输出返回值：
            无；Cart 分支始终精确要求 versioned blocker，reader
            blocker 可在当前 component receipt 有效时单独清除；artifact、
            pipeline 及其他协议各自只能使用
            与自身语义匹配的 pending blocker 闭集。
        """

        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        pending_rule = schema["$defs"]["taskSupport"]["allOf"][1]
        protocol_gate = pending_rule["then"]["allOf"][0]
        cart_blockers = protocol_gate["then"]["properties"]["blocker_codes"]
        artifact_gate = protocol_gate["else"]["allOf"][0]
        artifact_blockers = artifact_gate["then"]["properties"]["blocker_codes"]
        pipeline_gate = artifact_gate["else"]["allOf"][0]
        pipeline_blockers = pipeline_gate["then"]["properties"]["blocker_codes"]
        word_gate = pipeline_gate["else"]["allOf"][0]
        word_blockers = word_gate["then"]["properties"]["blocker_codes"]
        render_gate = word_gate["else"]["allOf"][0]
        render_blockers = render_gate["then"]["properties"]["blocker_codes"]
        other_blockers = render_gate["else"]["properties"]["blocker_codes"]

        self.assertEqual(
            "paraguibench.webmall.cart.closed-world.v1",
            protocol_gate["if"]["properties"]["evaluation_protocol"]["const"],
        )
        self.assertEqual(
            {
                "webmall_cart_reader_reference_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            },
            set(cart_blockers["items"]["enum"]),
        )
        self.assertEqual(
            {"const": "versioned_live_validation_not_completed"},
            cart_blockers["contains"],
        )
        self.assertEqual(
            [
                {
                    "enum": [
                        "webmall_cart_reader_reference_live_validation_not_completed",
                        "versioned_live_validation_not_completed",
                    ]
                },
                {"const": "versioned_live_validation_not_completed"},
            ],
            cart_blockers["prefixItems"],
        )
        self.assertEqual(1, cart_blockers["minContains"])
        self.assertEqual(1, cart_blockers["maxContains"])
        self.assertEqual(1, cart_blockers["minItems"])
        self.assertEqual(2, cart_blockers["maxItems"])
        self.assertEqual(
            {"const": "versioned_live_validation_not_completed"},
            artifact_blockers["contains"],
        )
        self.assertEqual(1, artifact_blockers["minContains"])
        self.assertEqual(1, artifact_blockers["maxContains"])
        self.assertEqual(1, artifact_blockers["minItems"])
        self.assertEqual(4, artifact_blockers["maxItems"])
        artifact_order_gate = artifact_blockers["allOf"][0]
        self.assertEqual(
            [
                {"const": ("osworld_artifact_getter_live_validation_not_completed")},
                {"const": ("osworld_artifact_gold_live_validation_not_completed")},
                {"const": "osworld_task_setup_live_validation_not_completed"},
                {"const": "versioned_live_validation_not_completed"},
            ],
            artifact_order_gate["then"]["prefixItems"],
        )
        self.assertEqual(
            [
                {"const": ("osworld_artifact_getter_live_validation_not_completed")},
                {"const": "versioned_live_validation_not_completed"},
            ],
            artifact_order_gate["else"]["then"]["prefixItems"],
        )
        self.assertEqual(
            [{"const": "versioned_live_validation_not_completed"}],
            artifact_order_gate["else"]["else"]["prefixItems"],
        )
        self.assertEqual(
            {
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            },
            set(artifact_blockers["items"]["enum"]),
        )

        self.assertEqual(
            {
                "pipeline_implicit_live_validation_not_completed",
                "versioned_live_validation_not_completed",
            },
            set(pipeline_blockers["items"]["enum"]),
        )
        self.assertEqual(
            {"const": "versioned_live_validation_not_completed"},
            pipeline_blockers["contains"],
        )
        self.assertEqual(1, pipeline_blockers["minContains"])
        self.assertEqual(1, pipeline_blockers["maxContains"])
        self.assertEqual(1, pipeline_blockers["minItems"])
        self.assertEqual(2, pipeline_blockers["maxItems"])
        pipeline_order_gate = pipeline_blockers["allOf"][0]
        self.assertEqual(
            [
                {"const": "pipeline_implicit_live_validation_not_completed"},
                {"const": "versioned_live_validation_not_completed"},
            ],
            pipeline_order_gate["then"]["prefixItems"],
        )
        self.assertEqual(
            [{"const": "versioned_live_validation_not_completed"}],
            pipeline_order_gate["else"]["prefixItems"],
        )
        self.assertEqual(
            [
                "Operation-FileOperate-BatchOperationWord-009",
                "Operation-FileOperate-BatchOperationWord-010",
            ],
            word_gate["if"]["properties"]["task_id"]["enum"],
        )
        self.assertEqual(
            [
                {
                    "const": (
                        "operation_word009_010_writer_live_validation_not_completed"
                    )
                },
                {"const": "versioned_live_validation_not_completed"},
            ],
            word_blockers["prefixItems"],
        )
        self.assertEqual(2, word_blockers["minItems"])
        self.assertEqual(2, word_blockers["maxItems"])
        self.assertEqual(
            "Operation-FileOperate-CombinationDocs-003",
            render_gate["if"]["properties"]["task_id"]["const"],
        )
        self.assertEqual(
            [
                {"const": ("combinationdocs003_real_render_validation_not_completed")},
                {"const": "versioned_live_validation_not_completed"},
            ],
            render_blockers["prefixItems"],
        )
        self.assertEqual(2, render_blockers["minItems"])
        self.assertEqual(2, render_blockers["maxItems"])
        self.assertEqual(
            [{"const": "versioned_live_validation_not_completed"}],
            other_blockers["prefixItems"],
        )
        self.assertEqual(1, other_blockers["minItems"])
        self.assertEqual(1, other_blockers["maxItems"])

    def test_cart_pending_schema_rejects_reversed_blocker_order(self) -> None:
        """确认公开 schema 本身拒绝 versioned 在前、reader 在后的反序。

        输入参数：无；用生成器构造合法 Cart pending 条目后
            只反转两个 blocker。
        输出返回值：无；JSON Schema 节点验证必须产生错误，
            不仅依赖生成器 expected-equality 或额外顺序校验。
        """

        task = json.loads(
            (
                REPO_ROOT
                / "benchmark/tasks/Operation-OnlineShopping-AddToCart-001.json"
            ).read_text(encoding="utf-8")
        )
        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=True,
            webmall_cart_component_ready=False,
        )
        entry["blocker_codes"] = list(reversed(entry["blocker_codes"]))
        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        errors: list[str] = []

        self.support_tool._validate_project_schema_node(
            root_schema=schema,
            node_schema=schema["$defs"]["taskSupport"],
            instance=entry,
            location="$task",
            errors=errors,
        )

        self.assertTrue(errors)

    def test_artifact_pending_schema_rejects_reversed_blocker_order(self) -> None:
        """确认 artifact pending 的 G/D/S/V 固定顺序由 schema 直接约束。

        输入参数：无；构造 image-ready 的正式 artifact-state
            条目，然后只反转 blocker 数组。
        输出返回：项目 JSON Schema 节点必须拒绝反序，不依赖
            生成器的额外 expected-equality 检查。
        """

        task = json.loads(
            (
                REPO_ROOT
                / "benchmark/tasks/Operation-FileOperate-BatchOperation-003.json"
            ).read_text(encoding="utf-8")
        )
        entry = self.support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=True,
        )
        self.assertEqual(4, len(entry["blocker_codes"]))
        entry["blocker_codes"] = list(reversed(entry["blocker_codes"]))
        schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        errors: list[str] = []

        self.support_tool._validate_project_schema_node(
            root_schema=schema,
            node_schema=schema["$defs"]["taskSupport"],
            instance=entry,
            location="$task",
            errors=errors,
        )

        self.assertTrue(errors)

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
            if entry["task_id"] == "InformationRetrieval-FileSearch-Readonly-002"
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
