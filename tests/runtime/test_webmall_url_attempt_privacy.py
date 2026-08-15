"""WebMall URL-multiset 经 AttemptRunner 到 RunStore 的纵向隐私测试。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import prepare_release_task
from paraguibench.runstore import EvaluationOutcome, RunStore
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.webmall_binding import preflight_webmall_runtime
from paraguibench.runtime.webmall_url_environment import (
    WebMallURLTaskEnvironment,
)


class _SensitiveURLAgent:
    """在内存中返回含 runtime origin 和重复商品的报告。"""

    def __init__(self, final_output: str, runtime_origin: str) -> None:
        """保存只允许 evaluator 读取的最终报告。

        输入参数：
            final_output：带敏感 runtime URL 的 Agent 终止文本。
            runtime_origin：用于验证 Agent 指令已物化、但不落盘的
                部署 origin。
        输出返回值：
            无。
        """

        self._final_output = final_output
        self._runtime_origin = runtime_origin

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """确认 Agent 只见物化指令后返回合法运行结果。

        输入参数：
            task_view：不含 logical gold 字段的 Agent 任务投影。
            environment：AttemptRunner 传入的 URL wrapper。
        输出返回值：
            含一步、固定终止理由与敏感最终文本的结果。
        """

        assert self._runtime_origin in str(task_view["instruction"])
        assert isinstance(environment, WebMallURLTaskEnvironment)
        return AgentRunResult(
            final_output=self._final_output,
            step_count=1,
            termination="finished",
        )


class _RawURLGUIEnvironment:
    """不访问 Docker 的最小 GUI 环境替身。"""

    def __init__(self) -> None:
        """初始化 Agent 可见 controller 和生命周期状态。

        输入参数：无。
        输出返回值：无。
        """

        self.controller = object()
        self.started = False
        self.prepared = False
        self.closed = False

    def start(self) -> None:
        """标记合成 GUI 环境已启动。

        输入参数：无。
        输出返回值：无。
        """

        self.started = True

    def prepare(self, task: Mapping[str, Any]) -> None:
        """验证 WebMall task 并标记环境已准备。

        输入参数：
            task：AttemptRunner 的可信 canonical task 投影。
        输出返回值：无。
        """

        assert self.started is True
        assert task["task_source"] == "WebMall"
        self.prepared = True

    def close(self) -> None:
        """标记合成 GUI 环境已关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.closed = True


def test_url_multiset_runtime_persists_only_protocol_counts_and_metrics(
    tmp_path: Path,
) -> None:
    """验证 URL 报告、origin、host 和商品身份不会进入 RunStore。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根。
    输出返回值：
        无；重复 URL 以普通评价失败落盘，所有文件只含
        协议、matched/wrong/missing 计数和三个指标。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task_id = "Operation-OnlineShopping-SingleProductSearch-001"
    runtime_origin = "https://private-url-store.example.invalid"
    origins = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            runtime_origin
            if index == 4
            else f"https://private-url-store-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    binding = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            task_id,
            environment_bindings={},
        ),
        environment=origins,
    )
    expected_logical = binding.prepared_task.trusted_task["expected_urls"][0]
    runtime_url = binding.registry.materialize_url(expected_logical)
    private_final_text = f"PRIVATE REPORT {runtime_url}###{runtime_url}"
    raw_environment = _RawURLGUIEnvironment()
    environment = WebMallURLTaskEnvironment(
        environment=raw_environment,
        registry=binding.registry,
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-webmall-url-privacy",
        run_record={"environment_id": binding.manifest.environment_id},
        version_vector=binding.version_vector,
    )
    attempt = store.start_attempt(
        run_id="run-webmall-url-privacy",
        task_id=task_id,
        attempt_id="attempt-001",
        task_record=binding.prepared_task.audit_metadata,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=binding.prepared_task,
        environment=environment,
        agent=_SensitiveURLAgent(private_final_text, runtime_origin),
        evaluator=binding.evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.FAILED
    assert raw_environment.closed is True
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in (
        runtime_origin,
        runtime_url,
        expected_logical,
        "PRIVATE REPORT",
        "hama-high-speed-hdmi-cable",
    ):
        assert sentinel.encode() not in persisted
    for expected_safe_field in (
        b"paraguibench.webmall.url-multiset.v1",
        b"matched_count",
        b"wrong_count",
        b"missing_count",
        b"precision",
        b"recall",
        b"f1",
    ):
        assert expected_safe_field in persisted
