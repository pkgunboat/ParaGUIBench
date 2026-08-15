"""OSWorld Chrome Bookmarks runtime adapter 与 registry 绑定测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paraguibench.evaluation.osworld import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_RULES,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)
from paraguibench.runtime.evaluators import (
    OSWorldBookmarkTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    build_task_evaluator,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]


class _BookmarkEnvironment:
    """向 runtime adapter 提供不落盘的书签 observation。"""

    def __init__(self, observations: tuple[object, ...]) -> None:
        """保存合成 observation 并初始化请求记录。

        输入参数：
            observations：评价阶段返回的逐 VM 不可变快照。
        输出返回值：
            无。
        """

        self._observations = observations
        self.requests: list[tuple[str, str]] = []

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """按 task/protocol 返回当前 Attempt 冻结的证据。

        输入参数：
            task_id：runtime adapter 固定的 canonical task ID。
            protocol_id：固定 Chrome Bookmarks 协议 ID。
        输出返回值：
            构造时提供的 observation tuple。
        """

        self.requests.append((task_id, protocol_id))
        return self._observations


def _steam_task() -> dict[str, Any]:
    """构造 WebNavigate-008 当前 release 的最小可信绑定。

    输入参数：
        无。
    输出返回值：
        与正式 task 身份字段一致、且不含答案的映射。
    """

    return {
        "task_id": "Operation-WebOperate-WebNavigate-008",
        "task_uid": "eb1ad6e6-b3cc-49e6-a633-a012ae38f56e",
        "task_source": "",
        "task_type": "self",
        "task_tag": "WebOperate",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
    }


def test_registry_evaluates_bookmarks_and_returns_allowlist_only_details() -> None:
    """验证正式 task 可经 environment seam 评价且详情不泄露状态。

    输入参数：
        无；使用含敏感 URL、文件夹名与 Agent 文本的完整单 VM 快照。
    输出返回值：
        无；adapter 满分通过，只返回固定协议、规则身份与计数。
    """

    task = _steam_task()
    sensitive_url = "https://store.steampowered.com/app/1238810/_5/"
    sensitive_folder = "PRIVATE BOOKMARK FOLDER"
    environment = _BookmarkEnvironment(
        (
            ChromeBookmarksObservation(
                records=(
                    ChromeBookmarkRecord(
                        url=sensitive_url,
                        folder_path=("bookmark_bar", sensitive_folder),
                    ),
                )
            ),
        )
    )

    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=CHROME_BOOKMARKS_PROTOCOL_ID,
    )
    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL OUTPUT",
        environment,
    )

    assert isinstance(evaluator, OSWorldBookmarkTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": CHROME_BOOKMARKS_PROTOCOL_ID,
        "task_rule_id": "Operation-WebOperate-WebNavigate-008",
        "reason_codes": (),
        "evaluated_vm_count": 1,
        "evaluator_error_vm_count": 0,
        "expected_target_count": 1,
        "matched_target_count": 1,
    }
    persisted_projection = repr(result.details)
    assert sensitive_url not in persisted_projection
    assert sensitive_folder not in persisted_projection
    assert "PRIVATE FINAL OUTPUT" not in persisted_projection
    assert environment.requests == [
        (
            "Operation-WebOperate-WebNavigate-008",
            CHROME_BOOKMARKS_PROTOCOL_ID,
        )
    ]


def test_registry_builds_bookmark_adapter_for_all_eleven_release_tasks() -> None:
    """验证全部 11 个正式 release task 均精确路由到同一原生协议。

    输入参数：
        无；直接读取 release 中的 canonical task 文件。
    输出返回值：
        无；每个任务都构造固定自身 task ID 的 Bookmark adapter。
    """

    for task_id in sorted(OSWORLD_BOOKMARK_TASK_RULES):
        task = json.loads(
            (_REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )

        evaluator = build_task_evaluator(
            task,
            evaluation_protocol=CHROME_BOOKMARKS_PROTOCOL_ID,
        )

        assert isinstance(evaluator, OSWorldBookmarkTaskEvaluator)


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("task_id", "Operation-WebOperate-WebNavigate-009"),
        ("task_uid", "00000000-0000-4000-a000-000000000000"),
        ("task_source", "OSWorld"),
        ("task_type", "OSWorld脚本"),
        ("task_tag", "QA"),
        ("evaluator_path", "eval/other.py"),
    ],
)
def test_registry_rejects_any_bookmark_identity_drift(
    field: str,
    drifted_value: str,
) -> None:
    """验证 task 身份任一字段漂移都在 VM 启动前失败关闭。

    输入参数：
        field/drifted_value：pytest 注入的被篡改字段与替代值。
    输出返回值：
        无；registry 必须拒绝构造 Bookmark adapter。
    """

    task = {**_steam_task(), field: drifted_value}

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            task,
            evaluation_protocol=CHROME_BOOKMARKS_PROTOCOL_ID,
        )
