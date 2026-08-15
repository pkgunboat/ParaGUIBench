"""OSWorld profile/active-tab runtime adapter 与 registry 绑定测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.osworld import (
    CHROME_PROFILE_NAME_PROTOCOL_ID,
    GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    ChromeProfileNameObservation,
    GoogleShoppingActiveTabObservation,
    OSWorldStateEvaluationError,
)
from paraguibench.runtime.evaluators import (
    OSWorldStateTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    build_task_evaluator,
)


class _OSWorldStateEnvironment:
    """向 runtime adapter 提供不落盘的逐 VM 状态 observation。"""

    def __init__(self, observations: tuple[object, ...]) -> None:
        """保存合成 observation 并初始化调用记录。

        输入参数：
            observations：评价阶段应返回的逐 VM 不可变快照。
        输出返回值：
            无。
        """

        self._observations = observations
        self.requested_protocols: list[str] = []

    def osworld_state_observations(
        self,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """按协议返回当前 Attempt 冻结的状态证据。

        输入参数：
            protocol_id：runtime adapter 固定的版本化协议 ID。
        输出返回值：
            构造时提供的 observation 元组。
        """

        self.requested_protocols.append(protocol_id)
        return self._observations


def _profile_task() -> dict[str, object]:
    """构造 Chrome profile canonical task 的最小可信元数据。

    输入参数：
        无。
    输出返回值：
        与正式 Settings-001 路由字段一致的映射。
    """

    return {
        "task_id": "Operation-WebOperate-Settings-001",
        "task_source": "OSWorld",
        "task_tag": "WebOperate",
        "evaluation_mode": "osworld_profile_state",
        "profile_state_adapter": "chrome_profile_name_v1",
        "vm_aggregation": "any_complete",
    }


def _active_tab_task() -> dict[str, object]:
    """构造 Google Shopping canonical task 的最小可信元数据。

    输入参数：
        无。
    输出返回值：
        与正式 WebNavigate-009 路由字段一致的映射。
    """

    return {
        "task_id": "Operation-WebOperate-WebNavigate-009",
        "task_source": "OSWorld",
        "task_tag": "WebOperate",
        "evaluation_mode": "osworld_active_tab",
        "active_tab_adapter": "google_shopping_selected_filters_v1",
        "vm_aggregation": "any_complete",
    }


def test_registry_builds_profile_adapter_and_returns_safe_details() -> None:
    """验证 Settings-001 精确绑定 profile 协议且不持久化名称原值。

    输入参数：
        无；使用正确 profile observation 与含敏感占位符的最终输出。
    输出返回值：
        无；通过结果只含固定协议、原因码和计数。
    """

    task = _profile_task()
    environment = _OSWorldStateEnvironment(
        (ChromeProfileNameObservation(profile_name="Thomas"),)
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=CHROME_PROFILE_NAME_PROTOCOL_ID,
    )

    result = evaluator.evaluate(task, "private-final-output", environment)

    assert isinstance(evaluator, OSWorldStateTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": CHROME_PROFILE_NAME_PROTOCOL_ID,
        "reason_codes": (),
        "evaluated_vm_count": 1,
        "evaluator_error_vm_count": 0,
        "missing_state_count": 0,
        "unexpected_state_count": 0,
    }
    assert "Thomas" not in repr(result.details)
    assert "private-final-output" not in repr(result.details)
    assert environment.requested_protocols == [CHROME_PROFILE_NAME_PROTOCOL_ID]


def test_registry_builds_google_shopping_adapter_and_ands_one_snapshot() -> None:
    """验证 WebNavigate-009 使用活动页协议而非 bookmark evaluator。

    输入参数：
        无；提供同一快照内正确 URL、query 与筛选闭集。
    输出返回值：
        无；adapter 满分通过且只请求 active-tab 协议证据。
    """

    task = _active_tab_task()
    environment = _OSWorldStateEnvironment(
        (
            GoogleShoppingActiveTabObservation(
                url=("https://www.google.com/search?tbm=shop&q=drip+coffee+maker"),
                locale="en-US",
                filter_surface_observed=True,
                selection_enumeration_complete=True,
                selection_evidence=("semantic_google_filter_state_list"),
                selected_filter_labels=(
                    "Black",
                    "$25 - $60",
                    "On sale",
                ),
            ),
        )
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    )

    result = evaluator.evaluate(task, "Done", environment)

    assert isinstance(evaluator, OSWorldStateTaskEvaluator)
    assert result.passed is True
    assert result.details["protocol_id"] == (GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID)
    assert environment.requested_protocols == [GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID]
    assert "google.com" not in repr(result.details)
    assert "Black" not in repr(result.details)


@pytest.mark.parametrize(
    ("task", "protocol"),
    [
        (_profile_task(), GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID),
        (_active_tab_task(), CHROME_PROFILE_NAME_PROTOCOL_ID),
        (
            {**_profile_task(), "profile_state_adapter": "unknown"},
            CHROME_PROFILE_NAME_PROTOCOL_ID,
        ),
        (
            {**_active_tab_task(), "vm_aggregation": "union"},
            GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
        ),
    ],
)
def test_registry_rejects_osworld_task_protocol_metadata_mismatch(
    task: dict[str, object],
    protocol: str,
) -> None:
    """验证 task、adapter、聚合语义与协议必须四向精确匹配。

    输入参数：
        task/protocol：故意错配的一组 canonical metadata。
    输出返回值：
        无；registry 在环境启动前失败关闭。
    """

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(task, evaluation_protocol=protocol)


def test_adapter_propagates_incomplete_evidence_as_evaluator_error() -> None:
    """验证 runtime 不把不完整 active-tab 证据降级成 score=0。

    输入参数：
        无；提供筛选枚举不完整的目标页 observation。
    输出返回值：
        无；纯 evaluator error 原样传播，交由 AttemptRunner 记 ERROR/null。
    """

    task = _active_tab_task()
    environment = _OSWorldStateEnvironment(
        (
            GoogleShoppingActiveTabObservation(
                url=("https://www.google.com/search?tbm=shop&q=drip+coffee+maker"),
                locale="en-US",
                filter_surface_observed=True,
                selection_enumeration_complete=False,
                selection_evidence="partial_filter_surface",
                selected_filter_labels=("Black",),
            ),
        )
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    )

    with pytest.raises(OSWorldStateEvaluationError):
        evaluator.evaluate(task, "Done", environment)


def test_adapter_rejects_environment_without_state_observation_interface() -> None:
    """验证错误 environment 不能让 state evaluator 静默读取其它证据。

    输入参数：
        无；使用没有 observation 接口的普通对象。
    输出返回值：
        无；adapter 抛固定 TypeError。
    """

    task = _profile_task()
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=CHROME_PROFILE_NAME_PROTOCOL_ID,
    )

    with pytest.raises(TypeError, match="observation"):
        evaluator.evaluate(task, "Done", object())
