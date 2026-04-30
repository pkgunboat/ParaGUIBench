"""
Shared helpers for normalizing GUI agent tool results.

GUI agents sometimes fail or time out after observing useful information. These
helpers keep that information structured so the planner can use it cautiously
without treating the subtask as completed.
"""

import json
import re
from typing import Any, Dict


DEFAULT_PARTIAL_EVIDENCE_MAX_CHARS = 1600

_PLACEHOLDER_TEXTS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "unknown",
    "no result",
    "no output",
    "no summary available",
    "execution failed",
    "task failed",
    "failed",
}


def _stringify(value: Any) -> str:
    """Convert common structured values to compact text for evidence fields."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_stack_trace_only(text: str) -> bool:
    """Return True when text appears to be only a Python exception traceback."""
    if not text:
        return False
    stripped = text.strip()
    if "traceback (most recent call last)" not in stripped.lower():
        return False

    informative_lines = []
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if (
            lower.startswith("traceback (most recent call last)")
            or lower.startswith("file ")
            or re.match(r"^[a-z_][\w.]*?(error|exception):", lower)
            or line.startswith("^")
            or lower.startswith("during handling of the above exception")
            or (raw_line[:1].isspace() and not lower.startswith(("found", "observed", "visible", "answer")))
        ):
            continue
        informative_lines.append(line)

    return not informative_lines


def _looks_informative(text: str, error_text: str = "") -> bool:
    """Filter empty strings, placeholders, duplicated errors, and pure stacks."""
    normalized = _normalize_whitespace(text)
    if not normalized:
        return False
    if normalized.lower() in _PLACEHOLDER_TEXTS:
        return False
    if error_text and normalized == _normalize_whitespace(error_text):
        return False
    if _is_stack_trace_only(text):
        return False
    return True


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "... [truncated]"


def _latest_step_text(result_data: Dict[str, Any], *, prefer_text_only: bool) -> str:
    """Extract the latest useful text from steps in reverse chronological order."""
    steps = result_data.get("steps") or []
    if not isinstance(steps, list):
        return ""

    error_text = _stringify(result_data.get("error"))
    for step in reversed(steps):
        output_text = ""
        if isinstance(step, dict):
            status = step.get("status")
            if prefer_text_only and status != "text_only":
                continue
            if not prefer_text_only and status == "text_only":
                continue
            output_text = _stringify(step.get("output"))
            if not output_text:
                output_text = _stringify(step.get("result"))
            if not output_text and not prefer_text_only:
                output_text = _stringify(step.get("thought"))
        elif isinstance(step, str) and not prefer_text_only:
            if len(_normalize_whitespace(step)) < 20:
                continue
            output_text = step

        if _looks_informative(output_text, error_text):
            return output_text.strip()

    return ""


def extract_partial_evidence(
    result_data: Any,
    max_chars: int = DEFAULT_PARTIAL_EVIDENCE_MAX_CHARS,
) -> str:
    """
    Extract useful, unverified evidence from a GUI tool result.

    Source priority:
      1. existing partial_evidence
      2. non-empty result that is not just the error
      3. latest steps[*].status == "text_only" output
      4. latest non-empty step output / result / thought / long string step
    """
    if not isinstance(result_data, dict):
        return ""

    error_text = _stringify(result_data.get("error"))
    candidates = [
        _stringify(result_data.get("partial_evidence")),
        _stringify(result_data.get("result")),
        _latest_step_text(result_data, prefer_text_only=True),
        _latest_step_text(result_data, prefer_text_only=False),
    ]

    for candidate in candidates:
        if _looks_informative(candidate, error_text):
            return _truncate(candidate, max_chars)

    return ""


def has_partial_evidence(result_data: Any) -> bool:
    """Return whether a tool result contains usable partial evidence."""
    return bool(extract_partial_evidence(result_data))


def _is_timeout_like(result_data: Dict[str, Any]) -> bool:
    text = " ".join(
        _stringify(result_data.get(key))
        for key in ("error", "result", "status")
    ).lower()
    timeout_markers = (
        "timeout",
        "timed out",
        "time out",
        "max round",
        "max_round",
        "maximum round",
        "maximum rounds",
        "reached maximum",
        "thread-level timeout",
    )
    return any(marker in text for marker in timeout_markers)


def classify_completion_state(result_data: Any) -> str:
    """
    Classify normalized completion state for planner policy.

    Values:
      - success
      - timeout_with_evidence
      - failure_with_evidence
      - failed_no_evidence
    """
    if not isinstance(result_data, dict):
        return "failed_no_evidence"

    if result_data.get("status") == "success":
        return "success"

    evidence = extract_partial_evidence(result_data)
    if _is_timeout_like(result_data):
        return "timeout_with_evidence" if evidence else "failed_no_evidence"
    return "failure_with_evidence" if evidence else "failed_no_evidence"


def normalize_result_metadata(result_data: Any) -> Dict[str, Any]:
    """
    Return a result dict with partial-evidence metadata populated.

    Non-dict values are wrapped as failures so callers can safely depend on the
    standard status/result/steps/error shape.
    """
    if isinstance(result_data, dict):
        normalized = dict(result_data)
    else:
        normalized = {
            "status": "failure",
            "result": "",
            "steps": [],
            "error": f"Unexpected result format: {type(result_data).__name__}",
        }

    normalized.setdefault("status", "failure")
    normalized.setdefault("result", "")
    normalized.setdefault("steps", [])
    normalized.setdefault("error", None if normalized.get("status") == "success" else "")

    evidence = extract_partial_evidence(normalized)
    normalized["partial_evidence"] = evidence
    normalized["has_partial_evidence"] = bool(evidence)
    normalized["completion_state"] = classify_completion_state(normalized)
    return normalized
