"""
只读 FileSearch 任务评估器。
对比 agent 输出与任务答案，支持基础的归一化与分词匹配。
支持结构化多值答案（分号分隔）解析。
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple, Union

_COMMON_SUFFIXES = (".docx", ".pdf", ".pptx", ".xlsx", ".csv", ".txt")

# Unicode 标点归一化：弯引号→ASCII 直引号、em/en dash→ASCII 连字符。
# 用于消除 ref/pred 因排版字符差异（如 Plan Agent 输出 "4+1" 而 ref 用 "4+1"）
# 导致的子串/精确匹配失配。
_PUNCT_NORMALIZE_MAP = str.maketrans({
    "\u201c": '"',  # LEFT  DOUBLE  QUOTATION MARK "
    "\u201d": '"',  # RIGHT DOUBLE  QUOTATION MARK "
    "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK „
    "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    "\u2018": "'",  # LEFT  SINGLE QUOTATION MARK '
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK '
    "\u201a": "'",  # SINGLE LOW-9 QUOTATION MARK ‚
    "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u00ab": '"',  # «
    "\u00bb": '"',  # »
    "\u2039": "'",  # ‹
    "\u203a": "'",  # ›
    "\u2013": "-",  # EN DASH    –
    "\u2014": "-",  # EM DASH    —
    "\u2015": "-",  # HORIZONTAL BAR ―
    "\u2212": "-",  # MINUS SIGN  −
})


def _remove_parentheses_content(text: str) -> str:
    """去除括号内的所有内容（包括中文和英文括号）。"""
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'（[^）]*）', '', text)
    return text.strip()


def _is_short_numeric(text: str) -> bool:
    """判断文本是否为短数字串（纯数字或小数，长度 <= 3）。"""
    return len(text) <= 3 and bool(re.fullmatch(r'\d+\.?\d*', text))


def _tokens_match(kw: str, pred_token: str) -> bool:
    """
    判断两个英文单词是否等价匹配（近似词干）。

    规则（按优先级）：
        1. 精确相等 → True
        2. 任一词长度 < 5 → 要求精确相等（短词不做近似）
        3. 两词长度差 <= 2，且共享前缀长度 >= min(len) - 2 且 >= 3 → True

    设计 trade-off：
        - 能覆盖：smartphones↔smartphone、categories↔category、
                  houses↔house、analysis↔analyses 等常见单复数变体。
        - 已知副作用：两词以相同前缀开头、长度差 <= 2 时会被视作等价。
                      常见误匹配包括 class↔classic、series↔serial、
                      summer↔summary、record↔recording、search↔searched、
                      history↔historic、writer↔written、physics↔physical 等。
                      这是规则的本质行为而非 bug；如需精细排除，需额外
                      白名单或引入真正的词干/词典库。本次接受此 trade-off
                      （baseline 任务实际影响小）。

    输入:
        kw: 参考答案中的关键词（已小写）
        pred_token: pred 中的一个英文 token（已小写）
    输出:
        bool: 是否视为等价
    """
    if kw == pred_token:
        return True
    if len(kw) < 5 or len(pred_token) < 5:
        return False
    common_prefix_len = min(len(kw), len(pred_token)) - 2
    if common_prefix_len < 3:
        return False
    return kw[:common_prefix_len] == pred_token[:common_prefix_len]


def _kv_substring_match(ref_part: str, pred_text: str) -> bool:
    """
    若 ref_part 为 'key:value' 格式，在 pred_text 中查找同 key 下的 value，
    检查两者是否互为子串（不区分大小写）。

    反例防护：短值（< MIN_VALUE_LEN）要求精确相等，避免 'brand:Sam'
    单向子串匹配 'brand:Samsung' 的陷阱。

    输入:
        ref_part: 参考答案的单个字段，如 "model:Doogee V Max"
        pred_text: 完整预测文本，如 "brand:DOOGEE;model:V Max"
    输出:
        bool: True 表示 key 存在且 value 存在非空子串关系
    """
    MIN_VALUE_LEN = 4
    m = re.match(r"^([^:]+):(.+)$", ref_part.strip())
    if not m:
        return False
    key = m.group(1).strip().lower()
    ref_val = m.group(2).strip().lower()
    if not ref_val:
        return False
    pred_m = re.search(
        rf"{re.escape(key)}\s*:\s*([^;]+)",
        pred_text or "",
        re.IGNORECASE,
    )
    if not pred_m:
        return False
    pred_val = pred_m.group(1).strip().lower()
    if not pred_val:
        return False
    if len(ref_val) < MIN_VALUE_LEN or len(pred_val) < MIN_VALUE_LEN:
        return ref_val == pred_val
    return ref_val in pred_val or pred_val in ref_val


def _keyword_match(reference: str, prediction: str) -> bool:
    """
    多关键词全部匹配：参考答案中的所有关键词都必须出现在预测中才通过。

    对纯数字关键词使用 word boundary 正则匹配。
    对非数字关键词：先查子串（保留原行为），若失败再做 token 级前缀匹配
    (P1-3 修复：smartphones ↔ smartphone 等单复数变体)。
    """
    keywords = [k.strip().lower() for k in reference.split(',') if k.strip()]
    if not keywords:
        return reference.lower() == prediction.lower()

    pred_lower = prediction.lower()
    pred_tokens = re.findall(r"[a-z]+", pred_lower)
    for kw in keywords:
        if _is_short_numeric(kw):
            if not re.search(r'\b' + re.escape(kw) + r'\b', pred_lower):
                return False
        else:
            if kw in pred_lower:
                continue
            # 仅对纯字母关键词做 token 前缀匹配；含标点/数字（如 "brand:samsung"）
            # 走严格子串路径，避免 KV 短值绕过 MIN_VALUE_LEN 防护。
            if kw.isalpha() and any(_tokens_match(kw, tok) for tok in pred_tokens):
                continue
            return False
    return True


def _contains_match(reference: str, prediction: str) -> bool:
    """
    包含匹配：双向子串包含。
    短数字答案禁用 contains 匹配以避免误匹配。
    """
    if _is_short_numeric(reference) or _is_short_numeric(prediction):
        return False
    ref_lower = reference.lower()
    pred_lower = prediction.lower()
    return ref_lower in pred_lower or pred_lower in ref_lower


def _try_interval_match(ref_part: str, pred_text: str) -> bool:
    """
    尝试区间匹配：适用于 Agent 输出带 ± 误差范围的物理量。

    参考答案格式：key: value（如 "up: 2.16"）
    Agent 输出格式：key:central±err1 或 key:central±err1±err2（如 "up:2.3±0.7±0.5"）

    如果参考值（或其 GeV↔MeV 换算值）落在 Agent 输出的区间内，返回 True。
    允许两种单位制（GeV 和 MeV）自动换算后再匹配。
    """
    # 解析参考答案：提取 key 和数值部分
    ref_m = re.match(r'^([a-zA-Z_]+):\s*([\d.]+)', ref_part.strip())
    if not ref_m:
        return False

    ref_key = ref_m.group(1).strip().lower()
    ref_val = float(ref_m.group(2))

    # 在预测文本中查找同名 key 的区间
    # 匹配 key:value±err1 或 key:value±err1±err2
    pattern = ref_key + r':\s*([\d.]+)\s*(?:±|±|±)\s*([\d.]+)\s*(?:±\s*([\d.]+))?'
    pred_m = re.search(pattern, pred_text, re.IGNORECASE)
    if not pred_m:
        return False

    try:
        central = float(pred_m.group(1))
        err1 = float(pred_m.group(2))
        err2 = float(pred_m.group(3)) if pred_m.group(3) else err1
    except ValueError:
        return False

    total_err = err1 + err2
    lo, hi = central - total_err, central + total_err

    # 直接比较
    if lo <= ref_val <= hi:
        return True

    # 尝试单位换算（GeV ↔ MeV，比例 1000）
    for scaled_val in [ref_val * 1000, ref_val / 1000]:
        if lo <= scaled_val <= hi:
            return True

    return False


def _load_task(task: Union[Dict, str]) -> Dict:
    """加载任务数据。"""
    if isinstance(task, dict):
        return task
    if isinstance(task, str):
        with open(task, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    raise TypeError(f"Unsupported task type: {type(task)}")


def _strip_common_suffixes(text: str) -> str:
    """去除常见文件后缀（不区分大小写）。"""
    lowered = text.lower()
    for suffix in _COMMON_SUFFIXES:
        if lowered.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _normalize_colon_spacing(text: str) -> str:
    """归一化冒号前后的空格，统一为 'key:value' 格式（无空格）。"""
    return re.sub(r'\s*:\s*', ':', text)


def _normalize_answer(text: Optional[str]) -> str:
    """
    归一化答案文本：strip → lower → Unicode 标点→ASCII → 冒号空格归一化 → 剥离常见文件后缀。
    标点归一化负责消除排版字符差异，使 ref/pred 在 substring/exact 比较时不被弯引号、
    em-dash 等字符误判失配。
    """
    normalized = (text or "").strip().lower()
    normalized = normalized.translate(_PUNCT_NORMALIZE_MAP)
    normalized = _normalize_colon_spacing(normalized)
    return _strip_common_suffixes(normalized).strip()


def _extract_answer_tag(text: Optional[str]) -> Optional[str]:
    """从模型输出中抽取 <answer>...</answer> 标签内容。"""
    if not text:
        return None
    match = re.search(r"<answer>(.*?)</answer>", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _parse_structured_answer(reference: str) -> Tuple[bool, List[str]]:
    """
    解析参考答案是否为分号分隔的多值格式。

    分号被视为多值分隔符。当参考答案包含分号时，
    视为结构化多值答案，每个分号片段作为一个独立匹配单元。

    返回:
        (is_structured, list_of_parts)
    """
    parts = [p.strip() for p in reference.split(';') if p.strip()]
    return len(parts) > 1, parts


def _match_single_part(ref_part: str, pred_text: str) -> bool:
    """
    匹配单个小项：精确匹配 -> 去除括号 -> 关键词 -> 区间 -> 包含 -> KV 子串（P1-1）。
    该函数仅返回 True/False，不决定最终 pass/fail。
    """
    ref_lower = ref_part.lower()
    pred_lower = pred_text.lower()
    if ref_lower == pred_lower:
        return True
    cleaned = _remove_parentheses_content(ref_part).strip().lower()
    if cleaned == pred_lower:
        return True
    if _keyword_match(ref_part, pred_text):
        return True
    if _try_interval_match(ref_part, pred_text):
        return True
    if _contains_match(ref_part, pred_text):
        return True
    if _kv_substring_match(ref_part, pred_text):
        return True
    return False


def _match_single_reference(
    reference: str,
    pred_text: str,
    pred_text_cleaned: str,
) -> Dict:
    """
    对一个 reference 候选执行单值匹配链，返回完整结果 dict。

    匹配顺序：精确 → 去括号精确 → KV 子串 → 关键词全匹配（含 precision 检查）
              → 包含（低置信度，pass=False）→ no_match。
    该函数不感知 accepted_answers，仅处理单一候选。

    输入:
        reference: 已 normalize 的参考答案候选
        pred_text: 已 normalize 的预测文本
        pred_text_cleaned: 去除括号内容后的 pred_text
    输出:
        dict: pass/score/reason/match_type/ref_text/pred_text 等字段
    """
    # 1. 精确匹配
    if pred_text == reference:
        return {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "精确匹配成功。",
            "match_type": "exact",
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # 2. 去除括号内容后精确匹配
    if pred_text_cleaned == reference:
        return {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "去除括号内容后精确匹配成功。",
            "match_type": "exact_after_parentheses_removal",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 3. 单值 KV 子串：ref='brand:Doogee', pred='brand:DOOGEE Pro' 应匹配。
    if ":" in reference and _kv_substring_match(reference, pred_text):
        return {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "单值 KV 子串匹配成功。",
            "match_type": "single_value_kv_substring",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 4. 多关键词全部匹配（附加 precision 检查）
    if _keyword_match(reference, pred_text_cleaned):
        pred_items = [
            x.strip() for x in re.split(r'[,，、]', pred_text_cleaned) if x.strip()
        ]
        ref_items = [
            x.strip() for x in re.split(r'[,，、]', reference) if x.strip()
        ]
        if len(pred_items) > len(ref_items):
            recall = 1.0
            precision = len(ref_items) / len(pred_items)
            f1 = 2 * precision * recall / (precision + recall)
            return {
                "pass": (f1 == 1.0),
                "score": f1,
                "status": "ok",
                "reason": (
                    f"关键词匹配成功但 Agent 多报：预期 {len(ref_items)} 项，"
                    f"Agent 给出 {len(pred_items)} 项，F1={f1:.4f}。"
                ),
                "match_type": "keyword_match_low_precision",
                "precision": precision,
                "f1": f1,
                "ref_text": reference,
                "pred_text": pred_text,
                "pred_text_cleaned": pred_text_cleaned,
            }
        return {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "多关键词全部匹配成功。",
            "match_type": "keyword_all_match",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 5. 包含匹配：降级为低置信度警告，不作为独立通过条件
    if _contains_match(reference, pred_text_cleaned):
        return {
            "pass": False,
            "score": 0.5,
            "status": "ok",
            "reason": "包含匹配触发（低置信度，不作为独立通过依据）。",
            "match_type": "contains_low_confidence",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 6. 全部不匹配
    return {
        "pass": False,
        "score": 0.0,
        "status": "ok",
        "reason": "匹配失败（精确匹配、关键词匹配均未通过）。",
        "match_type": "no_match",
        "ref_text": reference,
        "pred_text": pred_text,
        "pred_text_cleaned": pred_text_cleaned,
    }


def evaluate(task: Union[Dict, str], agent_answer: Optional[str]) -> Dict:
    """
    评估只读 FileSearch / QA 任务的答案是否匹配。

    采用两层策略：
    - 结构化多值（answer 包含分号）：按分号分割，逐项匹配，全部命中 pass
    - 单值（无分号）：精确 -> 关键词 -> 包含（低置信度警告）

    输入:
        task: 任务字典或任务 JSON 路径
        agent_answer: agent 输出答案
    输出:
        评估结果字典:
            - pass: True/False/None
            - score: float (0.0-1.0) 或 None
            - status: "ok"/"skip"
            - reason: 解释信息
            - match_type: 匹配类型（用于调试）
            - matched/total: 分项匹配统计（仅结构化多值时）
            - ref_text/pred_text: 用于调试
    """
    task_data = _load_task(task)

    # skip_eval: true 任务直接跳过
    if task_data.get("skip_eval"):
        return {
            "pass": None,
            "score": None,
            "status": "skip",
            "reason": "任务标记 skip_eval=true，跳过评估。",
            "ref_text": "",
            "pred_text": "",
        }

    reference = _normalize_answer(task_data.get("answer"))

    # 空参考答案特殊处理
    if reference == "":
        extracted = _extract_answer_tag(agent_answer)
        pred_text = _normalize_answer(extracted if extracted is not None else agent_answer)
        if pred_text == "" or not pred_text:
            return {
                "pass": None,
                "score": None,
                "status": "skip",
                "reason": "参考答案为空，跳过评估。",
                "ref_text": reference,
                "pred_text": pred_text,
            }
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": f"参考答案期望为空，但预测为 '{pred_text}'。",
            "match_type": "empty_reference_mismatch",
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # 提取预测答案（优先从 <answer> 标签提取）
    extracted = _extract_answer_tag(agent_answer)
    pred_text = _normalize_answer(extracted if extracted is not None else agent_answer)

    if not pred_text:
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "预测答案为空。",
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # 检测中止/错误信息
    _raw_answer = (agent_answer or "").strip().lower()
    if _raw_answer.startswith("[aborted]") or "fatal error" in _raw_answer[:200]:
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": f"Agent 执行中止或遇到致命错误，跳过匹配。",
            "match_type": "aborted",
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # INSUFFICIENT_EVIDENCE 短路：Agent 主动放弃作答（P0-4 配套）
    _raw_answer_clean = re.sub(r"[_\s]+", " ", _raw_answer)
    if (
        "insufficient evidence" in _raw_answer_clean
        or "insufficient_evidence" in _raw_answer
        or ">insufficient evidence<" in _raw_answer
    ):
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "Agent 主动放弃作答 (INSUFFICIENT_EVIDENCE)",
            "match_type": "agent_abstained",
            "ref_text": reference,
            "pred_text": _raw_answer,
        }

    pred_text_cleaned = _remove_parentheses_content(pred_text)

    # ---- 结构化多值分支（优先）----
    is_structured, parts = _parse_structured_answer(reference)
    if is_structured:
        # 同时解析 Agent 回答的分项，用于计算 precision
        _, pred_parts = _parse_structured_answer(pred_text_cleaned)
        if not pred_parts:
            pred_parts = [pred_text_cleaned]

        # Recall: 标准答案中有多少被 Agent 命中
        matched_count = sum(1 for p in parts if _match_single_part(p, pred_text_cleaned))
        recall = matched_count / len(parts) if parts else 0.0

        # Precision: Agent 回答的分项中有多少能匹配到标准答案
        pred_matched = sum(
            1 for pp in pred_parts
            if any(_match_single_part(rp, pp) for rp in parts)
        )
        precision = pred_matched / len(pred_parts) if pred_parts else 0.0

        # F1 score
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0.0

        is_pass = (f1 == 1.0)

        return {
            "pass": is_pass,
            "score": f1,
            "status": "ok",
            "reason": (
                f"结构化多值 F1 评估：recall={matched_count}/{len(parts)}, "
                f"precision={pred_matched}/{len(pred_parts)}, F1={f1:.4f}。"
            ),
            "match_type": "structured_f1_match" if is_pass else "structured_f1_partial",
            "matched": matched_count,
            "total": len(parts),
            "pred_parts_count": len(pred_parts),
            "pred_matched": pred_matched,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # ---- 单值分支 ----
    # 支持可选 accepted_answers 列表：先尝试 primary，再依次尝试 alias，
    # 任一 pass=True 即返回；全部失败回落到 primary 的结果。
    # 设计目的：缓解 ref 用文件名（如 paper3）但 GUI/Plan Agent 抽取语义内容
    # （如论文标题）的假阴性，evaluator 需要兼容同一信息的多种合法表达。
    aliases = task_data.get("accepted_answers") or []
    candidates: List[str] = [reference]
    for alias in aliases:
        norm = _normalize_answer(alias)
        if norm and norm not in candidates:
            candidates.append(norm)

    primary_result: Optional[Dict] = None
    for idx, ref_candidate in enumerate(candidates):
        result = _match_single_reference(ref_candidate, pred_text, pred_text_cleaned)
        if idx == 0:
            primary_result = result
        if result.get("pass"):
            if idx > 0:
                result["match_type"] = result["match_type"] + "_via_alias"
                result["matched_alias"] = ref_candidate
                result["reason"] = (
                    f"通过候选答案 #{idx} '{ref_candidate}' 匹配成功："
                    f"{result['reason']}"
                )
                # 面向用户的 ref_text 始终保留 primary，方便统计/调试一致性
                result["ref_text"] = reference
            return result

    # 全部候选均未 pass：返回基于 primary 的结果（含 contains_low_confidence 或 no_match）
    return primary_result
