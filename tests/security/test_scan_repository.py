"""仓库静态安全扫描器的回归测试。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCANNER_PATH = REPOSITORY_ROOT / "scripts" / "security" / "scan_repository.py"


def load_scanner_module():
    """功能：从脚本路径加载待测试模块，避免要求 scripts 成为可安装包。

    输入参数：无。
    输出返回值：已执行的 ``scan_repository`` Python 模块对象。
    """

    spec = importlib.util.spec_from_file_location("scan_repository", SCANNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载扫描器模块：{SCANNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepositorySecurityScannerTests(unittest.TestCase):
    """验证扫描准确性、输出脱敏和命令行退出码。"""

    @classmethod
    def setUpClass(cls) -> None:
        """功能：为整个测试类加载一次扫描器模块。

        输入参数：``cls`` 为当前测试类。
        输出返回值：无；模块保存在 ``cls.scanner``。
        """

        cls.scanner = load_scanner_module()

    def test_reports_secret_without_echoing_secret_value(self) -> None:
        """功能：确认高置信度 token 被发现，但报告不回显其值。

        输入参数：无；测试在临时目录中构造合成 token。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        synthetic_secret = "".join(("sk", "-proj-", "A" * 32))
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "settings.toml"
            source_file.write_text(
                f'api_key = "{synthetic_secret}"\n',
                encoding="utf-8",
            )

            findings = self.scanner.scan_file(source_file, Path(temporary_directory))
            report = self.scanner.format_findings(findings)

        self.assertEqual(1, len(findings))
        self.assertEqual("secret-token", findings[0].category)
        self.assertNotIn(synthetic_secret, report)
        self.assertIn("settings.toml:1", report)

    def test_reports_internal_home_path_without_echoing_path(self) -> None:
        """功能：确认开发者绝对路径被发现，同时不在报告中回显路径值。

        输入参数：无；测试动态拼接合成的开发者目录。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        internal_path = "".join(
            ("/", "home", "/", "example-developer", "/", "workspace")
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "config.yaml"
            source_file.write_text(f"workspace: {internal_path}\n", encoding="utf-8")

            findings = self.scanner.scan_file(source_file, Path(temporary_directory))
            report = self.scanner.format_findings(findings)

        self.assertEqual(1, len(findings))
        self.assertEqual("internal-path", findings[0].category)
        self.assertNotIn(internal_path, report)
        self.assertIn("config.yaml:1", report)

    def test_accepts_empty_environment_template_and_placeholders(self) -> None:
        """功能：确认空环境变量和显式占位符不会被误判为凭据。

        输入参数：无；测试使用临时 ``.env.example``。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / ".env.example"
            source_file.write_text(
                "OPENAI_API_KEY=\n"
                "ANTHROPIC_API_KEY=\n"
                "BENCH_VM_HOST=\n"
                "WORKSPACE=/home/<user>/ParaGUIBench\n",
                encoding="utf-8",
            )

            findings = self.scanner.scan_file(source_file, Path(temporary_directory))

        self.assertEqual([], findings)

    def test_repository_environment_template_contains_only_empty_values(self) -> None:
        """功能：确认仓库 ``.env.example`` 的变量均为空值。

        输入参数：无；读取公开模板而不读取进程环境或本地 ``.env``。
        输出返回值：无；任一变量携带非空值时由 unittest 报告变量名。
        """

        template_path = REPOSITORY_ROOT / ".env.example"
        assignments = []
        for line in template_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name, separator, value = stripped.partition("=")
            self.assertEqual("=", separator, msg=f"环境模板行缺少等号：{name}")
            assignments.append(name)
            self.assertEqual("", value, msg=f"环境模板变量不得带值：{name}")

        self.assertEqual(
            {
                "PARAGUIBENCH_MODEL_API_KEY",
                "PARAGUIBENCH_MODEL_BASE_URL",
                "PARAGUIBENCH_MODEL_ID",
                "PARAGUIBENCH_RUNS_ROOT",
                "PARAGUIBENCH_ASSET_CACHE_ROOT",
                "PARAGUIBENCH_GOLD_CACHE_ROOT",
                "PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN",
                "PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN",
                "PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN",
                "PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN",
                "PARAGUIBENCH_WEBMALL_STORE_1_READER_TARGET",
                "PARAGUIBENCH_WEBMALL_STORE_2_READER_TARGET",
                "PARAGUIBENCH_WEBMALL_STORE_3_READER_TARGET",
                "PARAGUIBENCH_WEBMALL_STORE_4_READER_TARGET",
                "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL",
                "PARAGUIBENCH_WEBMALL_LEASE_TOKEN",
                "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
                "PARAGUIBENCH_ONLYOFFICE_STATE_ROOT",
                "PARAGUIBENCH_ONLYOFFICE_HOST_IP",
                "PARAGUIBENCH_ONLYOFFICE_DOC_PORT",
                "PARAGUIBENCH_ONLYOFFICE_SHARE_PORT",
                "WP_CLI_DOCKER_NO_TTY",
            },
            set(assignments),
        )

    @unittest.skipUnless(shutil.which("git"), "需要 git 验证忽略规则")
    def test_candidate_collection_does_not_read_gitignored_environment_file(
        self,
    ) -> None:
        """功能：确认 Git 忽略的本地 ``.env`` 不进入静态扫描候选集。

        输入参数：无；测试在临时 Git 仓库中创建不含真实凭据的哨兵文件。
        输出返回值：无；若忽略文件被枚举则由 unittest 报告。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (root / ".gitignore").write_text(".env\n", encoding="utf-8")
            (root / ".env").write_text(
                "SYNTHETIC_VALUE=NOT_A_REAL_CREDENTIAL\n",
                encoding="utf-8",
            )
            (root / "safe.txt").write_text("safe\n", encoding="utf-8")

            candidates = self.scanner.collect_candidate_files(root)
            relative_candidates = {
                path.relative_to(root).as_posix() for path in candidates
            }

        self.assertNotIn(".env", relative_candidates)
        self.assertIn(".gitignore", relative_candidates)
        self.assertIn("safe.txt", relative_candidates)

    def test_reports_private_network_address_without_echoing_address(self) -> None:
        """功能：确认固定私网地址被发现但不会出现在报告中。

        输入参数：无；测试动态拼接合成的私网地址。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        internal_address = ".".join(("10", "23", "45", "67"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "deployment.yaml"
            source_file.write_text(f"host: {internal_address}\n", encoding="utf-8")

            findings = self.scanner.scan_file(source_file, Path(temporary_directory))
            report = self.scanner.format_findings(findings)

        self.assertEqual(1, len(findings))
        self.assertEqual("internal-host", findings[0].category)
        self.assertNotIn(internal_address, report)

    def test_main_returns_nonzero_without_printing_matched_value(self) -> None:
        """功能：确认命令行发现问题时返回非零且标准输出保持脱敏。

        输入参数：无；测试以临时目录作为扫描根目录。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        synthetic_secret = "".join(("hf", "_", "B" * 32))
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "credentials.txt"
            source_file.write_text(synthetic_secret, encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.scanner.main(["--root", temporary_directory])

        self.assertEqual(1, exit_code)
        self.assertNotIn(synthetic_secret, stdout.getvalue())
        self.assertIn("secret-token", stdout.getvalue())

    def test_methods_zone_exempts_environment_coupled_rules_only(self) -> None:
        """功能：方法区内私网地址豁免，但真实凭据规则仍然命中。

        输入参数：无；以临时目录构造方法区文件。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            zone_file = root / "src" / "parallel_benchmark" / "tasks" / "demo.json"
            zone_file.parent.mkdir(parents=True)
            internal_address = "192.168" + ".1.10"
            zone_file.write_text(
                '{"shop_url": "http://' + internal_address + '/shop"}\n',
                encoding="utf-8",
            )
            findings = self.scanner.scan_file(zone_file, root)
            self.assertEqual([], findings)

            synthetic_secret = "sk-ant-" + "a" * 20
            zone_file.write_text(
                '{"note": "' + synthetic_secret + '"}\n',
                encoding="utf-8",
            )
            findings = self.scanner.scan_file(zone_file, root)
            self.assertEqual(1, len(findings))
            self.assertEqual("secret-token", findings[0].category)

    def test_methods_services_zone_exempts_environment_coupled_rules_only(self) -> None:
        """功能：deploy/methods-services/ 与方法区同策略，凭据规则仍命中。

        输入参数：无；以临时目录构造服务栈文件。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            zone_file = (
                root / "deploy" / "methods-services" / "onlyoffice" / "demo.py"
            )
            zone_file.parent.mkdir(parents=True)
            internal_address = "192.168" + ".1.10"
            zone_file.write_text(
                'UPSTREAM = "http://' + internal_address + ':8080"\n',
                encoding="utf-8",
            )
            findings = self.scanner.scan_file(zone_file, root)
            self.assertEqual([], findings)

            synthetic_secret = "sk-ant-" + "a" * 20
            zone_file.write_text(
                'TOKEN = "' + synthetic_secret + '"\n',
                encoding="utf-8",
            )
            findings = self.scanner.scan_file(zone_file, root)
            self.assertEqual(1, len(findings))
            self.assertEqual("secret-token", findings[0].category)

    def test_non_zone_files_keep_full_rule_set(self) -> None:
        """功能：方法区外的私网地址仍然按原规则报告。

        输入参数：无；以临时目录构造普通文件。
        输出返回值：无；断言失败时由 unittest 报告。
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            public_file = root / "docs" / "example.md"
            public_file.parent.mkdir(parents=True)
            internal_address = "192.168" + ".1.10"
            public_file.write_text(
                "server at " + internal_address + "\n",
                encoding="utf-8",
            )
            findings = self.scanner.scan_file(public_file, root)
            self.assertEqual(1, len(findings))
            self.assertEqual("internal-host", findings[0].category)


if __name__ == "__main__":
    unittest.main()
