"""QA 答案提取与 Plan Agent 总结阶段的格式契约。

本模块只依赖 Python 标准库，便于在不加载 Agent、OpenAI 客户端或
虚拟机组件的情况下独立测试关键提示与短路规则。
"""

from __future__ import annotations

import re
from typing import Optional


_SUMMARY_SENTINELS = frozenset({"INSUFFICIENT_EVIDENCE"})


def extract_obvious_short_answer(text: str, max_chars: int = 49) -> Optional[str]:
    """Extract a local short answer only when it is not a narrative sentence.

    功能：对不含 answer 标签的短文本执行保守本地判断；可剝离 ``The answer
    is``、``Answer:`` 和 ``Final answer:`` 三类明确前缀，但对“I found”、
    “task completed”等过程叙述返回 None，使调用方继续使用提取模型。
    输入参数：text 为 GUI Agent 原始短输出；max_chars 为本地短路的最大
    字符数，默认 49。
    输出返回值：可确认的纯答案文本；不应本地短路时返回 None。
    """
    candidate = str(text or "").strip()
    if not candidate or len(candidate) > max_chars or "\n" in candidate:
        return None

    prefixed = re.fullmatch(
        r"(?:the\s+)?(?:final\s+)?answer\s*(?:is\s+|:\s*)(.+)",
        candidate,
        flags=re.IGNORECASE,
    )
    if prefixed is None:
        prefixed = re.fullmatch(
            r"(?:final\s+)?result\s*:\s*(.+)",
            candidate,
            flags=re.IGNORECASE,
        )
    if prefixed:
        extracted = prefixed.group(1).strip()
        if not extracted or re.search(r"[.!?]$", extracted):
            return None
        return extracted

    # 完整句式（包括 “The winner is BYD” 和 “BYD is the answer”）即使很短，
    # 也必须交给带任务上下文的提取模型；否则 exact 模式会把整句误作答案。
    if re.search(r"\b(?:is|are|was|were)\b", candidate, flags=re.IGNORECASE):
        return None
    if re.search(r"[.!?]$", candidate):
        return None

    narrative_prefixes = (
        r"i\b",
        r"we\b",
        r"this\b",
        r"it\b",
        r"there\b",
        r"based\s+on\b",
        r"according\s+to\b",
        r"after\b",
        r"found\b",
        r"the\s+(?:file|document|result|value|number|name)\s+(?:is|was)\b",
        r"task\s+(?:is\s+)?(?:complete|completed|done)\b",
        r"(?:done|completed|finished|successfully\s+completed)\b",
    )
    if any(
        re.match(pattern, candidate, flags=re.IGNORECASE)
        for pattern in narrative_prefixes
    ):
        return None
    return candidate


def build_plan_summary_prompt() -> str:
    """Create the Plan Agent prompt that extracts a task-faithful final answer.

    功能：要求总结模型从全部 GUI 证据中提取最终答案，并严格保留
    原任务要求的键、分隔符、顺序、单位与文件扩展名；只在原任务明示
    要求文件主名时才允许省略扩展名。
    输入参数：无。
    输出返回值：可直接发送给 Plan Agent 总结模型的英文提示文本。
    """
    return (
        "The task execution is now complete. Based on ALL the information gathered "
        "from the GUI agents above, provide the FINAL ANSWER to the original task.\n\n"
        "CRITICAL RULES:\n"
        "1. Wrap the answer in <answer></answer> tags.\n"
        "2. Extract the answer from the evidence; do not paraphrase or normalize it.\n"
        "3. Preserve the exact format required by the original task, including key "
        "names, delimiters, item order, units, and file extensions. Do not add, remove, "
        "reorder, translate, or substitute these elements.\n"
        "4. For a file name, retain the extension exactly when it appears in the "
        "evidence or is required by the task. Return only the file stem solely when "
        "the original task explicitly requests a stem.\n"
        "5. If the original task does not specify a delimiter for multiple items, use "
        "semicolons.\n"
        "6. Keep only the requested answer value inside the tags; do not include "
        "explanations or extra context.\n"
        "7. Review ALL execution rounds before answering.\n"
        "8. Answer in English unless the original task explicitly requires another "
        "language."
    )


def build_gui_answer_extraction_prompt(
    task_instruction: str,
    final_answer: str,
    steps_context: str = "",
) -> str:
    """Build the GUI-only post-processing prompt without changing answer syntax.

    功能：把任务指令、GUI Agent 总结与可选步骤上下文组合为答案提取
    提示；明确禁止改写 keys、delimiters、order、units 和 file extensions。
    输入参数：task_instruction 为原任务指令；final_answer 为 GUI Agent 的
    原始总结；steps_context 为可选的近期执行步骤摘要。
    输出返回值：可直接发送给答案提取模型的中文提示文本。
    """
    instruction = str(task_instruction or "")
    summary = str(final_answer or "")[:2000]
    context = str(steps_context or "")[:3000]
    return (
        "你是一个严格的答案提取器，只能从现有证据中复制最终答案，"
        "不得改写、推测、翻译或格式化答案。\n\n"
        f"任务指令：\n{instruction}\n\n"
        f"GUI Agent 执行总结：\n{summary}{context}\n\n"
        "请只输出任务要求的答案值，不要添加 <answer> 标签或解释。"
        "必须严格保留证据中的 keys、delimiters、item order、units 和 "
        "file extensions，包括分号、逗号、冒号以及大小写；不得增删字段、"
        "调换顺序、替换分隔符、换算单位或删除文件扩展名。"
        "仅当任务指令明示要求另一种格式时，才按指令的格式输出。"
        "如果证据完全不足以得出答案，输出 unknown。"
    )


def should_short_circuit_summary(
    final_answer: Optional[str],
    *,
    explicit_answer_tag: bool = False,
) -> bool:
    """Decide whether Plan Agent may skip the final summary extraction call.

    功能：仅当当前非空答案来自显式 ``<answer>`` 标签，或完整等于已登记
    的弃答哨兵值时允许跳过总结；“task completed”等叙述性文本不具有短路
    资格。
    输入参数：final_answer 为 recorder 当前答案；explicit_answer_tag 表示该值是否
    由最后一个完整 answer 标签提取。
    输出返回值：允许跳过总结模型时返回 True，否则返回 False。
    """
    answer = str(final_answer or "").strip()
    if not answer:
        return False
    if explicit_answer_tag:
        return True
    normalized = answer.upper().replace(" ", "_")
    return normalized in _SUMMARY_SENTINELS
