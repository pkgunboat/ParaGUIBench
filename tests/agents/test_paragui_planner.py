"""ParaGUI 结构化 planner adapter 的严格解析与聚合测试。"""

from __future__ import annotations

from typing import Any

from paraguibench.agents.systems.paragui import StructuredParaGUIPlanner
from paraguibench.framework import SubtaskResult, SubtaskStatus


def test_structured_planner_builds_validated_plan_and_safe_synthesis_view() -> None:
    """验证 backend JSON 被转为 DAG，聚合只接收显式结果字段。

    输入参数：
        无；backend 返回两个独立节点和一个依赖节点。
    输出返回值：
        无；plan 字段、依赖与 synthesis projection 均符合公开契约。
    """

    class Backend:
        """记录调用参数的合成 planning backend。"""

        def __init__(self) -> None:
            """初始化 synthesis 调用记录。

            输入参数：
                无。
            输出返回值：
                无。
            """

            self.result_view: tuple[dict[str, Any], ...] = ()

        def create_plan(self, task_view: dict[str, Any]) -> dict[str, Any]:
            """返回严格结构化的 planner 数据。

            输入参数：
                task_view：gold-free Agent task view。
            输出返回值：
                含三个 subtask object 的 JSON 兼容字典。
            """

            assert "answer" not in task_view
            return {
                "subtasks": [
                    {
                        "id": "a",
                        "instruction": "Inspect A.",
                        "depends_on": [],
                        "worker_role": "gui",
                    },
                    {
                        "id": "b",
                        "instruction": "Inspect B.",
                        "depends_on": [],
                        "worker_role": "gui",
                    },
                    {
                        "id": "merge",
                        "instruction": "Merge.",
                        "depends_on": ["a", "b"],
                        "worker_role": "gui",
                    },
                ]
            }

        def create_answer(
            self,
            task_view: dict[str, Any],
            result_view: tuple[dict[str, Any], ...],
        ) -> str:
            """读取 allowlist 结果并返回最终 answer。

            输入参数：
                task_view：gold-free Agent task view。
                result_view：不含异常对象的结构化 subtask 投影。
            输出返回值：
                evaluator 可消费的最终文本。
            """

            assert task_view["task_id"] == "synthetic"
            self.result_view = result_view
            return "<answer>done</answer>"

    backend = Backend()
    planner = StructuredParaGUIPlanner(backend=backend)
    task_view = {"task_id": "synthetic", "instruction": "Inspect both."}

    plan = planner.plan(task_view)
    answer = planner.synthesize(
        task_view,
        (
            SubtaskResult("a", SubtaskStatus.SUCCEEDED, "A", 2),
            SubtaskResult("b", SubtaskStatus.SUCCEEDED, "B", 3),
            SubtaskResult("merge", SubtaskStatus.SUCCEEDED, "AB", 1),
        ),
    )

    assert tuple(item.subtask_id for item in plan.subtasks) == (
        "a",
        "b",
        "merge",
    )
    assert plan.subtasks[-1].depends_on == ("a", "b")
    assert answer == "<answer>done</answer>"
    assert backend.result_view[-1] == {
        "subtask_id": "merge",
        "status": "SUCCEEDED",
        "output": "AB",
        "step_count": 1,
        "failure_type": None,
    }


def test_structured_planner_rejects_extra_fields_without_echoing_values() -> None:
    """验证 malformed planner 数据 fail closed 且异常不回显字段值。

    输入参数：
        无；backend 在节点中注入未允许字段和 synthetic sentinel。
    输出返回值：
        无；解析抛错，异常文本只指出字段契约而不包含 sentinel。
    """

    sentinel = "planner-output-secret-sentinel"

    class Backend:
        """返回含未知字段的合成 backend。"""

        def create_plan(self, task_view: dict[str, Any]) -> dict[str, Any]:
            """生成非法节点。

            输入参数：
                task_view：本测试未使用的 task view。
            输出返回值：
                含未允许 ``debug`` 字段的字典。
            """

            del task_view
            return {
                "subtasks": [
                    {
                        "id": "node",
                        "instruction": "Inspect.",
                        "depends_on": [],
                        "worker_role": "gui",
                        "debug": sentinel,
                    }
                ]
            }

        def create_answer(
            self,
            task_view: dict[str, Any],
            result_view: tuple[dict[str, Any], ...],
        ) -> str:
            """提供未使用的 synthesis 方法。

            输入参数：
                task_view：Agent task view。
                result_view：subtask 投影。
            输出返回值：
                空字符串。
            """

            del task_view, result_view
            return ""

    try:
        StructuredParaGUIPlanner(backend=Backend()).plan(
            {"task_id": "synthetic", "instruction": "Inspect."}
        )
    except ValueError as error:
        assert "fields" in str(error)
        assert sentinel not in str(error)
    else:
        raise AssertionError("unknown planner field must be rejected")
