from parallel_benchmark.parallel_agents.plan_agent_thought_action import (
    _last_executed_round_all_failed,
    _last_executed_round_has_partial_evidence,
    _local_clean_answer,
    _should_set_insufficient_evidence_fallback,
    _warn_if_answer_after_all_failed_attempts,
)
from parallel_benchmark.parallel_agents_as_tools.result_utils import (
    classify_completion_state,
    extract_partial_evidence,
    normalize_result_metadata,
)


class DummyLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


def _execution_log_with_gui_results(*results):
    return {
        "rounds": [
            {
                "tool_calls": [{"id": "call-1", "function": "call_gui_agent"}],
                "results": [
                    {
                        "tool_call_id": f"result-{i}",
                        "function": "call_gui_agent",
                        **result,
                    }
                    for i, result in enumerate(results)
                ],
            }
        ]
    }


def test_explicit_answer_is_preserved_after_all_failed_gui_round():
    execution_log = _execution_log_with_gui_results(
        {"status": "failure", "has_partial_evidence": False}
    )
    logger = DummyLogger()

    answer = _local_clean_answer("8")
    _warn_if_answer_after_all_failed_attempts(execution_log, answer, logger)

    assert answer == "8"
    assert logger.warnings
    assert "preserving the explicit answer" in logger.warnings[0]


def test_all_failed_round_with_partial_evidence_does_not_abstain():
    result = normalize_result_metadata(
        {
            "status": "failure",
            "result": "The visible page shows 8 matching rows.",
            "steps": [],
            "error": "Reached maximum rounds (3) without completing the task.",
        }
    )
    execution_log = _execution_log_with_gui_results(
        {
            "status": result["status"],
            "completion_state": result["completion_state"],
            "has_partial_evidence": result["has_partial_evidence"],
            "partial_evidence": result["partial_evidence"],
        }
    )

    assert _last_executed_round_all_failed(execution_log)
    assert _last_executed_round_has_partial_evidence(execution_log)
    assert not _should_set_insufficient_evidence_fallback(execution_log, "")


def test_failure_result_field_becomes_partial_evidence():
    result = {
        "status": "failure",
        "result": "Found candidate answer: EUR.",
        "steps": [],
        "error": "Agent stopped before writing the value.",
    }

    assert extract_partial_evidence(result) == "Found candidate answer: EUR."
    normalized = normalize_result_metadata(result)
    assert normalized["has_partial_evidence"] is True
    assert normalized["completion_state"] == "failure_with_evidence"


def test_text_only_step_becomes_partial_evidence():
    result = {
        "status": "failure",
        "result": "",
        "steps": [
            {"status": "executed", "output": "Clicked search"},
            {
                "status": "text_only",
                "output": "The page reports the final count as 42.",
            },
        ],
        "error": "Agent reported task as infeasible",
    }

    assert extract_partial_evidence(result) == "The page reports the final count as 42."
    assert classify_completion_state(result) == "failure_with_evidence"


def test_failed_round_without_valid_evidence_allows_abstain_fallback():
    result = normalize_result_metadata(
        {
            "status": "failure",
            "result": "",
            "steps": [
                {"status": "executed", "output": ""},
                "click button",
            ],
            "error": "Screenshot capture failed",
        }
    )
    execution_log = _execution_log_with_gui_results(
        {
            "status": result["status"],
            "completion_state": result["completion_state"],
            "has_partial_evidence": result["has_partial_evidence"],
            "partial_evidence": result["partial_evidence"],
        }
    )

    assert result["has_partial_evidence"] is False
    assert result["completion_state"] == "failed_no_evidence"
    assert _should_set_insufficient_evidence_fallback(execution_log, "")


def test_timeout_with_result_is_classified_as_timeout_with_evidence():
    result = {
        "status": "failure",
        "result": "Best visible answer before timeout: Samsung.",
        "steps": [],
        "error": "Timeout after 30s.",
    }

    assert classify_completion_state(result) == "timeout_with_evidence"


def test_pure_traceback_is_not_partial_evidence():
    result = {
        "status": "failure",
        "result": "",
        "steps": [
            {
                "status": "executed",
                "output": (
                    "Traceback (most recent call last):\n"
                    "  File \"agent.py\", line 10, in run\n"
                    "    value = rows[0]\n"
                    "IndexError: list index out of range"
                ),
            }
        ],
        "error": "IndexError: list index out of range",
    }

    assert extract_partial_evidence(result) == ""
