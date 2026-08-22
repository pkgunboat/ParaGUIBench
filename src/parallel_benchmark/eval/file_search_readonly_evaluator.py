"""
只读 FileSearch 任务评估器。
对比 agent 输出与任务答案，支持基础的归一化与分词匹配。
支持结构化多值答案（分号分隔）解析。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple, Union

try:
    from parallel_benchmark.eval.answer_extraction import extract_last_complete_answer_tag
    from parallel_benchmark.eval.qa_run_contracts import build_skip_evaluation
except ImportError:
    from answer_extraction import extract_last_complete_answer_tag  # type: ignore[no-redef]
    from qa_run_contracts import build_skip_evaluation  # type: ignore[no-redef]

_COMMON_SUFFIXES = (".docx", ".pdf", ".pptx", ".xlsx", ".csv", ".txt")

IntervalPolicy = Tuple[Decimal, Tuple[str, ...]]

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
    """移除三类括号组并折叠空白。

    功能：只为受限昵称兼容逻辑生成候选基串；调用方必须先验证括号内容，不能
    把本函数的输出直接作为通用匹配文本，否则会删除否定词或关键限定条件。
    输入参数：text，可能包含英文圆括号、方括号或中文圆括号的文本。
    输出返回值：删除完整括号组并把连续空白折叠为单空格后的字符串。
    """
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'（[^）]*）', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _has_parenthetical_group(text: str) -> bool:
    """判断文本是否包含本评价器可识别的完整括号组。

    功能：为匹配链提供失败关闭信号，避免未获准的括号内容继续进入关键词或
    KV 子串路径。
    输入参数：text，待扫描的规范化文本。
    输出返回值：存在英文圆括号、方括号或中文圆括号完整组时返回 True。
    """
    return bool(re.search(r"\([^)]*\)|\[[^\]]*\]|（[^）]*）", text or ""))


def _remove_supported_parenthetical_alias(
    reference: str,
    prediction: str,
) -> Optional[str]:
    """仅移除可由参考姓名词元支持的括号昵称。

    功能：兼容 ``Edwin (Ed) Catmull`` 这类括号昵称，同时拒绝 ``BYD
    (or Tesla)``、``Foo (UK)`` 等候选注入或限定词替换。括号中的每个词元都
    必须是某个参考词元的前缀，且移除括号后的基串必须与参考答案完全一致。
    输入参数：reference 为规范化参考答案；prediction 为规范化预测答案。
    输出返回值：验证通过时返回移除括号后的预测；不含括号或验证失败时返回
    None。
    """
    group_patterns = (r"\(([^)]*)\)", r"\[([^\]]*)\]", r"（([^）]*)）")
    contents: List[str] = []
    for pattern in group_patterns:
        contents.extend(re.findall(pattern, prediction or ""))
    if not contents:
        return None

    cleaned_prediction = _remove_parentheses_content(prediction)
    if cleaned_prediction != reference:
        return None

    reference_tokens = re.findall(r"[^\W_]+", reference, flags=re.UNICODE)
    if not reference_tokens:
        return None
    for content in contents:
        content_tokens = re.findall(r"[^\W_]+", content, flags=re.UNICODE)
        if not content_tokens:
            return None
        for token in content_tokens:
            if len(token) < 2:
                return None
            if not any(ref_token.startswith(token) for ref_token in reference_tokens):
                return None
    return cleaned_prediction


def _is_short_numeric(text: str) -> bool:
    """判断文本是否为短数字字面量。

    功能：识别旧版包含匹配中需要特殊保护的长度不超过 3 的无符号整数或小数。
    输入参数：text，待判断的规范化文本。
    输出返回值：文本满足短数字格式时返回 True，否则返回 False。
    """
    return len(text) <= 3 and bool(re.fullmatch(r'\d+\.?\d*', text))


def _parse_numeric_literal(text: str) -> Optional[Decimal]:
    """把完整数字字面量解析为 Decimal。

    功能：仅接受带可选正负号的十进制整数或小数，拒绝在单位、范围或其他
    文本中猜测数值，供 numeric 模式和 KV 数值执行无精度损失的相等比较。
    输入参数：text，待判断的文本。
    输出返回值：合法数字返回 Decimal；不是完整数字字面量时返回 None。
    """
    candidate = (text or "").strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", candidate):
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _keyword_occurs_as_complete_span(keyword: str, prediction: str) -> bool:
    """判断关键词是否以完整 Unicode 词边界出现在预测文本中。

    功能：替代裸子串检查，阻止 seven→seventeen、Xperia 1 V→Xperia 1 VI
    以及 THE:177→THE:1770 等前后缀扩展，同时保留自然语言句中的关键词。
    输入参数：keyword 为参考关键词；prediction 为完整预测文本。
    输出返回值：关键词左右均不紧邻字母、数字或下划线时返回 True。
    """
    normalized_keyword = (keyword or "").strip().lower()
    normalized_prediction = (prediction or "").lower()
    if not normalized_keyword:
        return False
    pattern = rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)"
    return bool(re.search(pattern, normalized_prediction, flags=re.UNICODE))


def _has_unexpected_assertion_markers(reference: str, prediction: str) -> bool:
    """检测预测中相对标答新增的否定或候选连接标记。

    功能：识别“BYD or Tesla”“not Poland”等把正确字符串放入冲突断言的
    情况；标答自身已有的 and、or、斜杠等不会被误判。
    输入参数：reference 为归一化标答；prediction 为归一化预测。
    输出返回值：预测新增任一高风险标记时返回 True，否则返回 False。
    """
    ref_lower = (reference or "").lower()
    pred_lower = (prediction or "").lower()
    marker_patterns = (
        r"\bno\b",
        r"\bnot\b",
        r"\bwithout\b",
        r"\bneither\b",
        r"\bnor\b",
        r"\beither\b",
        r"\binstead(?:\s+of)?\b",
        r"\bversus\b",
        r"\bvs\.?\b",
        r"\band\b",
        r"\bor\b",
        r"\bplus\b",
        r"\bmaybe\b",
        r"\bperhaps\b",
        r"\bapproximately\b",
        r"\bapprox\.?\b",
        r"\babout\b",
        r"\baround\b",
        r"\bbetween\b",
        r"\bto\b",
        r"\bextra\b",
    )
    for pattern in marker_patterns:
        if len(re.findall(pattern, pred_lower)) > len(re.findall(pattern, ref_lower)):
            return True
    for marker in (";", "/", "&", "?", "±", "~"):
        if pred_lower.count(marker) > ref_lower.count(marker):
            return True
    return False


def _numeric_context_match(
    reference: str,
    prediction: str,
    accepted_units: Optional[List[str]] = None,
) -> bool:
    """按白名单语法比较 numeric 模式的数值答案。

    功能：预测必须是一个完整数字，或是“数字 + 任务显式声明单位”；因此比较
    符、近似词、数量级、运算词、问号和额外叙述均无法进入数值比较。
    输入参数：reference 为完整数字字面量；prediction 为完整预测文本；
    accepted_units 为任务允许的单位字符串列表，缺省时不允许任何尾随文本。
    输出返回值：预测语法合法且 Decimal 数值与参考值相等时返回 True。
    """
    reference_number = _parse_numeric_literal(reference)
    if reference_number is None:
        return False

    number_pattern = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    normalized_units = [
        str(unit).strip().lower()
        for unit in (accepted_units or [])
        if str(unit).strip()
    ]
    if normalized_units:
        unit_pattern = "|".join(
            re.escape(unit) for unit in sorted(set(normalized_units), key=len, reverse=True)
        )
        match = re.fullmatch(
            rf"\s*({number_pattern})(?:\s+({unit_pattern}))?\s*",
            prediction or "",
            flags=re.IGNORECASE,
        )
    else:
        match = re.fullmatch(rf"\s*({number_pattern})\s*", prediction or "")
    if not match:
        return False
    prediction_number = _parse_numeric_literal(match.group(1))
    return prediction_number is not None and prediction_number == reference_number


def _simple_singular_token(token: str) -> str:
    """把常见英文复数词元转换为保守的单数候选。

    功能：仅处理 ``-s``、``-es`` 和 ``-ies`` 三类规则复数，用于兼容
    smartphone/smartphones；不再按共享前缀猜测词干，避免 series/serial 等
    不同词被误判等价。
    输入参数：token，已转为小写的纯英文字母词元。
    输出返回值：规则可识别时返回单数候选，否则原样返回。
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes", "ses")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens_match(kw: str, pred_token: str) -> bool:
    """判断两个英文词元是否为完全相同或规则单复数变体。

    功能：用保守的单复数归一化替代共享前缀启发式，保留
    smartphone/smartphones 等合法差异，并拒绝 series/serial、smart/smarter
    等仅前缀相似但语义不同的词。
    输入参数：kw 为小写参考词元；pred_token 为小写预测词元。
    输出返回值：两词完全相同或规则单数候选相同时返回 True。
    """
    if kw == pred_token:
        return True
    if not kw.isalpha() or not pred_token.isalpha():
        return False
    if abs(len(kw) - len(pred_token)) > 2:
        return False
    return _simple_singular_token(kw) == _simple_singular_token(pred_token)


def _kv_substring_match(ref_part: str, pred_text: str) -> bool:
    """按完整 ``key:value`` 记录比较一个 KV 分项。

    功能：要求参考与预测都只包含一个完整 KV 记录且键完全相等；数值值使用
    Decimal 精确比较，文本值仅允许预测以完整边界包含参考值。该约束阻止键
    后缀、重复字段、额外字段、否定值和错误型号截断绕过。
    输入参数：ref_part 为参考 KV 分项；pred_text 为待比较的单个预测 KV 分项。
    输出返回值：键相同且值满足数值精确或受边界文本包含时返回 True。
    """
    MIN_VALUE_LEN = 4
    reference_match = re.fullmatch(r"\s*([^:;]+?)\s*:\s*([^;]+?)\s*", ref_part or "")
    prediction_match = re.fullmatch(r"\s*([^:;]+?)\s*:\s*([^;]+?)\s*", pred_text or "")
    if not reference_match or not prediction_match:
        return False
    reference_key = reference_match.group(1).strip().lower()
    prediction_key = prediction_match.group(1).strip().lower()
    if not reference_key or reference_key != prediction_key:
        return False
    ref_val = reference_match.group(2).strip().lower()
    pred_val = prediction_match.group(2).strip().lower()
    if not ref_val or not pred_val:
        return False
    if _has_unexpected_assertion_markers(ref_val, pred_val):
        return False
    reference_mass = _parse_explicit_mass_quantity(ref_val)
    prediction_mass = _parse_explicit_mass_quantity(pred_val)
    if reference_mass is not None or prediction_mass is not None:
        return (
            reference_mass is not None
            and prediction_mass is not None
            and reference_mass == prediction_mass
        )
    ref_number = _parse_numeric_literal(ref_val)
    pred_number = _parse_numeric_literal(pred_val)
    if ref_number is not None or pred_number is not None:
        return (
            ref_number is not None
            and pred_number is not None
            and ref_number == pred_number
        )
    if len(ref_val) < MIN_VALUE_LEN or len(pred_val) < MIN_VALUE_LEN:
        return ref_val == pred_val
    return _keyword_occurs_as_complete_span(ref_val, pred_val)


def _keyword_match(reference: str, prediction: str) -> bool:
    """判断参考答案中的全部逗号关键词是否完整出现。

    功能：所有关键词先执行完整 Unicode 边界匹配；纯字母词失败后仅允许保守
    单复数变体，以兼容 smartphones/smartphone 并阻止超串和近前缀误判。
    输入参数：reference 为逗号分隔参考文本；prediction 为完整预测文本。
    输出返回值：每个非空参考关键词均匹配时返回 True，否则返回 False。
    """
    keywords = [k.strip().lower() for k in reference.split(',') if k.strip()]
    if not keywords:
        return reference.lower() == prediction.lower()

    pred_lower = prediction.lower()
    pred_tokens = re.findall(r"[a-z]+", pred_lower)
    for kw in keywords:
        if _keyword_occurs_as_complete_span(kw, pred_lower):
            continue
        # 仅对纯字母关键词做 token 近似匹配；含标点/数字（如 "brand:samsung"）
        # 必须满足完整边界，避免绕过 KV 数值和短值防护。
        if kw.isalpha() and any(_tokens_match(kw, tok) for tok in pred_tokens):
            continue
        return False
    return True


def _contains_match(reference: str, prediction: str) -> bool:
    """执行只用于诊断的双向包含检查。

    功能：判断任一文本是否为另一文本的子串；短数字禁用该路径。调用方只能把
    命中视为低置信度，不得据此独立判为通过。
    输入参数：reference 为参考文本；prediction 为预测文本。
    输出返回值：非短数字文本存在双向子串关系时返回 True，否则返回 False。
    """
    if _is_short_numeric(reference) or _is_short_numeric(prediction):
        return False
    ref_lower = reference.lower()
    pred_lower = prediction.lower()
    return ref_lower in pred_lower or pred_lower in ref_lower


def _try_interval_match(
    ref_part: str,
    pred_text: str,
    interval_policy: Optional[IntervalPolicy] = None,
) -> bool:
    """按完整键、显式单位和受限误差宽度比较物理量区间。

    功能：默认完全禁止 Agent 自报误差区间。只有调用方传入由任务
    ``allow_interval=true``、``interval_max_relative_width`` 和 ``interval_units``
    联合构建的 policy 时，才接受 ``key:central±err``；两侧必须显式写出
    任务白名单单位，且相对总误差不得超过任务上限。
    输入参数：ref_part 为单个参考 KV 数值；pred_text 为单个带误差预测 KV；
    interval_policy 为“最大相对宽度、允许单位”二元组，缺省时禁用。
    输出返回值：参考值经必要的显式单位换算后落入合法窄区间时返回 True。
    """
    if interval_policy is None:
        return False
    max_relative_width, allowed_units = interval_policy

    number_pattern = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    error_pattern = r"(?:\d+(?:\.\d*)?|\.\d+)"
    reference_match = re.fullmatch(
        rf"\s*([a-zA-Z_][\w-]*)\s*:\s*({number_pattern})\s*(GeV|MeV)?\s*",
        ref_part or "",
        flags=re.IGNORECASE,
    )
    if not reference_match:
        return False

    reference_key = reference_match.group(1).strip().lower()
    prediction_match = re.fullmatch(
        rf"\s*{re.escape(reference_key)}\s*:\s*({number_pattern})\s*±\s*"
        rf"({error_pattern})(?:\s*±\s*({error_pattern}))?\s*(GeV|MeV)?\s*",
        pred_text or "",
        flags=re.IGNORECASE,
    )
    if not prediction_match:
        return False

    reference_value = _parse_numeric_literal(reference_match.group(2))
    central_value = _parse_numeric_literal(prediction_match.group(1))
    first_error = _parse_numeric_literal(prediction_match.group(2))
    second_error = _parse_numeric_literal(prediction_match.group(3) or "0")
    if None in (reference_value, central_value, first_error, second_error):
        return False

    total_error = first_error + second_error
    if central_value == 0:
        if total_error != 0:
            return False
    elif total_error / abs(central_value) > max_relative_width:
        return False

    reference_unit = (reference_match.group(3) or "").lower()
    prediction_unit = (prediction_match.group(4) or "").lower()
    normalized_allowed_units = {unit.lower() for unit in allowed_units}
    if (
        not reference_unit
        or not prediction_unit
        or reference_unit not in normalized_allowed_units
        or prediction_unit not in normalized_allowed_units
    ):
        return False
    comparable_reference = reference_value
    if reference_unit and prediction_unit and reference_unit != prediction_unit:
        if reference_unit == "gev" and prediction_unit == "mev":
            comparable_reference *= Decimal("1000")
        elif reference_unit == "mev" and prediction_unit == "gev":
            comparable_reference /= Decimal("1000")
        else:
            return False

    lower_bound = central_value - total_error
    upper_bound = central_value + total_error
    return lower_bound <= comparable_reference <= upper_bound


def _parse_interval_policy(
    task_data: Dict,
) -> Tuple[Optional[IntervalPolicy], Optional[Dict]]:
    """解析任务显式声明的误差区间白名单。

    功能：未声明 ``allow_interval=true`` 时返回禁用 policy；显式启用时
    要求同时提供严格介于 0 与 1 之间的 ``interval_max_relative_width``
    和非空 ``interval_units`` 字符串列表，否则返回 evaluator_error。
    输入参数：task_data 为已加载任务配置字典。
    输出返回值：二元组；第一项为可选 interval policy，第二项为可选
    评价器配置错误结果，两者不会同时非空。
    """
    if task_data.get("allow_interval") is not True:
        return None, None

    raw_width = task_data.get("interval_max_relative_width")
    raw_units = task_data.get("interval_units")
    try:
        max_relative_width = Decimal(str(raw_width))
    except (InvalidOperation, TypeError, ValueError):
        max_relative_width = Decimal("-1")
    valid_units = (
        isinstance(raw_units, (list, tuple))
        and bool(raw_units)
        and all(isinstance(unit, str) and unit.strip() for unit in raw_units)
    )
    if not (Decimal("0") < max_relative_width <= Decimal("1")) or not valid_units:
        return None, {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": (
                "allow_interval=true 必须同时提供 (0,1] 范围内的 "
                "interval_max_relative_width 和非空 interval_units 字符串列表。"
            ),
            "match_type": "invalid_interval_policy",
            "ref_text": _canonical_exact_answer(task_data.get("answer")),
            "pred_text": "",
        }
    units = tuple(str(unit).strip() for unit in raw_units)
    return (max_relative_width, units), None


def _load_task(task: Union[Dict, str]) -> Dict:
    """从字典或 JSON 路径加载任务配置。

    功能：保留已解析字典，或以 UTF-8 读取 JSON 文件；其他输入类型明确报错。
    输入参数：task，任务字典或 JSON 文件路径字符串。
    输出返回值：可供评价器读取的任务字典。
    """
    if isinstance(task, dict):
        return task
    if isinstance(task, str):
        with open(task, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    raise TypeError(f"Unsupported task type: {type(task)}")


def _strip_common_suffixes(text: str) -> str:
    """去除 legacy 匹配允许忽略的常见文件后缀。

    功能：不区分大小写地移除一个末尾 docx/pdf/pptx/xlsx/csv/txt 后缀；严格
    exact 模式不会调用本函数。
    输入参数：text，待处理文本。
    输出返回值：命中后缀时返回去后缀文本，否则原样返回。
    """
    lowered = text.lower()
    for suffix in _COMMON_SUFFIXES:
        if lowered.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _normalize_colon_spacing(text: str) -> str:
    """归一化 KV 冒号两侧空白与并列排名记号。

    功能：把任意数量的冒号前后空白统一为 ``key:value`` 形式；仅对
    ``key:=value`` 这一明确 KV 并列排名形式剝离冒号后的单个等号，不改写
    独立等号或其他运算符。
    输入参数：text，可能含不一致冒号空格的文本。
    输出返回值：冒号空格规范化后的字符串。
    """
    normalized = re.sub(r"\s*:\s*=\s*", ":", text)
    return re.sub(r"\s*:\s*", ":", normalized)


def _normalize_mass_energy_unit_notation(text: str) -> str:
    """归一化 MeV/GeV 质量单位中 ``c²`` 的常见纯文本写法。

    功能：只处理显式写出 MeV 或 GeV、斜杠和 c 平方的单位片段，把
    ``MeV/c²``、``MeV/c^2``、``MeV/c2`` 及空格变体统一为 ``mev/c2``；
    不补全缺失单位，也不执行 GeV/MeV 数值换算。
    输入参数：text 为已完成基础标点归一化的答案文本。
    输出返回值：仅单位排版被窄归一化后的文本。
    """
    return re.sub(
        r"\b(mev|gev)\s*/\s*c(?:\s*\^\s*2|²|2)(?!\w)",
        lambda match: f"{match.group(1).lower()}/c2",
        text,
        flags=re.IGNORECASE,
    )


def _parse_explicit_mass_quantity(text: str) -> Optional[Tuple[Decimal, str]]:
    """解析完整的 MeV/GeV 质量值，拒绝尾随误差或不确定叙述。

    功能：接受且只接受“单个十进制数 + 显式 MeV/GeV/c² 单位”的完整值；
    常见 c 平方排版先做窄归一化。``±999``、``maybe``、范围和额外文本因
    无法完整匹配而失败，单位缺失也不会被猜测补全。
    输入参数：text 为一个 KV value 字符串。
    输出返回值：合法时返回 ``(Decimal 数值, canonical 单位)``，否则 None。
    """
    candidate = _normalize_mass_energy_unit_notation(str(text or "").strip().lower())
    match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mev|gev)/c2",
        candidate,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    number = _parse_numeric_literal(match.group(1))
    if number is None:
        return None
    return number, f"{match.group(2).lower()}/c2"


def _strip_one_outer_quote_pair(text: str) -> str:
    """剝离一层成对外引号而保留内部引号。

    功能：在 Unicode 弯引号已转为 ASCII 后，允许模型用一对单引号或双引号
    包裹整个答案；函数仅删除最外一层，不修改中间字符。
    输入参数：text 为已执行标点归一化的答案文本。
    输出返回值：去掉一层成对外引号的文本；无成对外引号时原样返回。
    """
    candidate = str(text or "").strip()
    if len(candidate) >= 2 and candidate[0] in {"'", '"'}:
        if candidate[-1] == candidate[0]:
            return candidate[1:-1].strip()
    return candidate


def _normalize_answer(text: Optional[str]) -> str:
    """生成 legacy 匹配使用的规范化答案。

    功能：依次执行去首尾空白、小写化、Unicode 标点转 ASCII、冒号空格归一、
    常见文件后缀剥离及连续空白折叠。严格模式使用另一 canonical 函数。
    输入参数：text，原始答案文本或 None。
    输出返回值：legacy 匹配使用的规范化字符串。
    """
    normalized = (text or "").strip().lower()
    normalized = normalized.translate(_PUNCT_NORMALIZE_MAP)
    normalized = _strip_one_outer_quote_pair(normalized)
    normalized = _normalize_mass_energy_unit_notation(normalized)
    normalized = _normalize_colon_spacing(normalized)
    normalized = _strip_common_suffixes(normalized).strip()
    return re.sub(r'\s+', ' ', normalized)


def _canonical_exact_answer(text: Optional[str]) -> str:
    """生成 exact 模式专用 canonical 文本，保留文件扩展名。

    功能：统一大小写、Unicode 标点、冒号和结构化分隔符空格，但刻意不调用
    `_strip_common_suffixes`，避免 report.docx 与 report.pdf 被折叠为同一答案。
    输入参数：text，原始任务答案、别名或从最终 answer 标签提取的预测文本。
    输出返回值：可用于 exact 比较的 canonical 字符串。
    """
    normalized = (text or "").strip().lower().translate(_PUNCT_NORMALIZE_MAP)
    normalized = _strip_one_outer_quote_pair(normalized)
    normalized = _normalize_mass_energy_unit_notation(normalized)
    normalized = _normalize_colon_spacing(normalized)
    normalized = re.sub(r"\s*([;,:/])\s*", r"\1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_answer_tag(text: Optional[str]) -> Optional[str]:
    """从模型输出中抽取最后一个完整 answer 标签。

    功能：当模型先给出草稿再修正时，以最后一个完整标签作为最终答案；不完整标签
    不参与匹配。
    输入参数：text，模型完整输出或 None。
    输出返回值：最后一个完整标签的去空白内容；不存在完整标签时返回 None。
    """
    return extract_last_complete_answer_tag(text)


def _is_abstention_answer(text: Optional[str]) -> bool:
    """判断最终选中答案是否完整等于弃答哨兵。

    功能：统一空格与下划线，并允许可选方括号；只做整串匹配，避免历史叙述中
    曾出现 ``insufficient evidence`` 就覆盖后续正确结论。
    输入参数：text，最终 answer 标签内容或无标签时的完整最终输出。
    输出返回值：文本完整表示 INSUFFICIENT_EVIDENCE 时返回 True。
    """
    normalized = re.sub(r"[_\s]+", " ", (text or "").strip().lower())
    return bool(re.fullmatch(r"\[?insufficient evidence\]?", normalized))


def _parse_structured_answer(reference: str) -> Tuple[bool, List[str]]:
    """解析分号分隔的结构化参考答案。

    功能：去除空分项；至少存在两个非空分项时才标记为结构化多值答案。
    输入参数：reference，已规范化的参考答案。
    输出返回值：二元组；第一项表示是否结构化，第二项为非空分项列表。
    """
    parts = [p.strip() for p in reference.split(';') if p.strip()]
    return len(parts) > 1, parts


def _parse_keyed_numeric_sets(text: Optional[str]) -> Optional[Dict[str, Tuple[int, ...]]]:
    """解析 ``key:n,n;key:n`` 格式的无序页码集合。

    功能：要求键唯一、值均为非负整数、同一键内无重复值；键和分项顺序不影响
    语义，但任何额外键、额外值或重复值都会保留为不等价证据。
    输入参数：text，待解析的完整 keyed numeric set 文本。
    输出返回值：成功时返回键到已排序整数元组的映射；格式或唯一性失败时返回
    None。
    """
    candidate = (text or "").strip()
    if not candidate:
        return None
    raw_parts = candidate.split(";")
    if any(not part.strip() for part in raw_parts):
        return None

    parsed: Dict[str, Tuple[int, ...]] = {}
    for part in raw_parts:
        match = re.fullmatch(
            r"\s*([^:;]+?)\s*:\s*(\d+(?:\s*,\s*\d+)*)\s*",
            part,
        )
        if not match:
            return None
        key = match.group(1).strip().lower()
        if not key or key in parsed:
            return None
        values = [int(value.strip()) for value in match.group(2).split(",")]
        if len(values) != len(set(values)):
            return None
        parsed[key] = tuple(sorted(values))
    return parsed


def _match_single_part(
    ref_part: str,
    pred_text: str,
    interval_policy: Optional[IntervalPolicy] = None,
) -> bool:
    """按结构化分项的严格契约匹配一个参考项和一个预测项。

    功能：允许精确、括号清理、受控 KV、数值区间和等长 token 形态变化；禁止
    keyword/contains 回落，避免 Brawl Stars 2 或错误 KV 超串冒充正确项。
    输入参数：ref_part 为一个归一化参考分项；pred_text 为一个归一化预测分项；
    interval_policy 为任务显式误差区间契约，缺省时禁用区间匹配。
    输出返回值：两个完整分项语义等价时返回 True，否则返回 False。
    """
    ref_lower = ref_part.lower()
    pred_lower = pred_text.lower()
    if ref_lower == pred_lower:
        return True
    supported_parenthetical = _remove_supported_parenthetical_alias(
        ref_lower,
        pred_lower,
    )
    if supported_parenthetical == ref_lower:
        return True
    if _has_parenthetical_group(ref_lower) or _has_parenthetical_group(pred_lower):
        return False
    if _try_interval_match(ref_part, pred_text, interval_policy):
        return True
    if _has_unexpected_assertion_markers(ref_lower, pred_lower):
        return False
    if ":" in ref_part:
        return _kv_substring_match(ref_part, pred_text)
    ref_tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?", ref_lower)
    pred_tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?", pred_lower)
    if not ref_tokens or len(ref_tokens) != len(pred_tokens):
        return False
    for ref_token, pred_token in zip(ref_tokens, pred_tokens):
        if ref_token == pred_token:
            continue
        if (
            ref_token.isalpha()
            and pred_token.isalpha()
            and _tokens_match(ref_token, pred_token)
        ):
            continue
        return False
    return True


def _split_structured_prediction(
    prediction: str,
    reference_parts: List[str],
) -> Tuple[List[str], int]:
    """按 reference-aware 规则拆分结构化预测项。

    功能：先按任务规定的分号拆分；若某段并非完整参考实体，再按 and/& 拆分。
    ``or`` 仍拆出候选用于部分得分，但另记一项语义冲突，不能冒充确定列表。
    完整等于参考实体的 ``Trinidad and Tobago`` 等名称保持整体。
    输入参数：prediction 为归一化预测；reference_parts 为分号解析后的参考分项。
    输出返回值：二元组；第一项是保留重复项和原顺序的预测分项列表，第二项是
    非参考实体片段中出现的 ``or`` 冲突数，用于扩大 precision 分母。
    """
    primary_parts = [part.strip() for part in prediction.split(";") if part.strip()]
    result: List[str] = []
    disjunction_conflicts = 0
    normalized_references = {part.strip().lower() for part in reference_parts}
    for part in primary_parts:
        if part.lower() in normalized_references:
            result.append(part)
            continue
        disjunction_conflicts += len(
            re.findall(r"\bor\b", part, flags=re.IGNORECASE)
        )
        split_parts = [
            item.strip()
            for item in re.split(r"\s+(?:and|or)\s+|\s*&\s*", part, flags=re.IGNORECASE)
            if item.strip()
        ]
        result.extend(split_parts or [part])
    return result, disjunction_conflicts


def _maximum_structured_matches(
    reference_parts: List[str],
    prediction_parts: List[str],
    interval_policy: Optional[IntervalPolicy] = None,
) -> int:
    """计算参考分项与预测分项之间的一对一最大匹配数。

    功能：使用增广路算法消费每个参考项至多一次，防止重复预测项多次命中同一
    标答；列表规模来自短答案，确定性搜索开销可忽略。
    输入参数：reference_parts 为参考分项；prediction_parts 为预测分项；
    interval_policy 为可选任务级误差区间契约。
    输出返回值：满足 `_match_single_part` 的最大一对一配对数量。
    """
    matched_prediction_by_reference: Dict[int, int] = {}

    def _augment(prediction_index: int, seen_references: set[int]) -> bool:
        """为指定预测项寻找一条可用的一对一增广路径。

        功能：遍历尚未访问的参考项；若参考项已被占用，则递归尝试把原预测项
        迁移到其他参考项，从而得到当前预测项可加入的最大匹配。
        输入参数：prediction_index 为待匹配预测项下标；seen_references 为本次
        搜索中已访问的参考项下标集合，用于防止循环搜索。
        输出返回值：成功新增或重排出一个匹配时返回 True，否则返回 False。
        """
        for reference_index, reference_part in enumerate(reference_parts):
            if reference_index in seen_references:
                continue
            if not _match_single_part(
                reference_part,
                prediction_parts[prediction_index],
                interval_policy,
            ):
                continue
            seen_references.add(reference_index)
            previous_prediction = matched_prediction_by_reference.get(reference_index)
            if previous_prediction is None or _augment(
                previous_prediction,
                seen_references,
            ):
                matched_prediction_by_reference[reference_index] = prediction_index
                return True
        return False

    match_count = 0
    for prediction_index in range(len(prediction_parts)):
        if _augment(prediction_index, set()):
            match_count += 1
    return match_count


def _evaluate_ordered_structured_candidate(
    reference: str,
    prediction: str,
    interval_policy: Optional[IntervalPolicy] = None,
) -> Dict:
    """Evaluate a semicolon list while preserving the reference order.

    功能：使用与 legacy 结构化匹配相同的单项语义规则，但要求预测分项数、
    分号顺序和每个对应分项全部一致；任何额外项、缺失项、顺序反转或
    ``or`` 对冲都不得通过。
    输入参数：reference 为 canonical 分号参考答案；prediction 为 canonical
    预测答案；interval_policy 为可选任务级误差区间契约。
    输出返回值：包含 pass、score、匹配数和顺序诊断的完整评价字典。
    """
    is_structured, reference_parts = _parse_structured_answer(reference)
    if not is_structured:
        return {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": "ordered_structured 参考答案必须含至少两个分号分项。",
            "match_type": "invalid_ordered_structured_reference",
            "ref_text": reference,
            "pred_text": prediction,
        }

    prediction_parts, disjunction_conflicts = _split_structured_prediction(
        prediction,
        reference_parts,
    )
    pair_matches = [
        _match_single_part(reference_part, prediction_part, interval_policy)
        for reference_part, prediction_part in zip(reference_parts, prediction_parts)
    ]
    matched = sum(1 for item in pair_matches if item)
    is_pass = (
        disjunction_conflicts == 0
        and len(prediction_parts) == len(reference_parts)
        and matched == len(reference_parts)
    )
    return {
        "pass": is_pass,
        "score": matched / len(reference_parts),
        "status": "ok",
        "reason": (
            "顺序结构化匹配："
            f"matched_in_order={matched}/{len(reference_parts)}, "
            f"prediction_items={len(prediction_parts)}, "
            f"disjunction_conflicts={disjunction_conflicts}。"
        ),
        "match_type": (
            "ordered_structured"
            if is_pass
            else "ordered_structured_no_match"
        ),
        "matched": matched,
        "total": len(reference_parts),
        "pred_parts_count": len(prediction_parts),
        "disjunction_conflicts": disjunction_conflicts,
        "ref_text": reference,
        "pred_text": prediction,
    }


def _match_single_reference(
    reference: str,
    pred_text: str,
    pred_text_cleaned: str,
) -> Dict:
    """对一个参考候选执行失败关闭的单值匹配链。

    功能：依次执行精确、受限括号昵称、断言冲突检查、完整 KV、关键词和低置信
    包含匹配。KV 参考一旦解析失败不会回落到关键词路径；未获准的括号内容也不
    会被删除后继续匹配。
    输入参数：reference 为归一化参考候选；pred_text 为归一化预测；
    pred_text_cleaned 仅保留为结果诊断字段，不再作为匹配依据。
    输出返回值：包含 pass、score、status、reason、match_type 和诊断文本的字典。
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

    # 2. 仅允许能由参考姓名词元支持的括号昵称。
    supported_parenthetical = _remove_supported_parenthetical_alias(
        reference,
        pred_text,
    )
    if supported_parenthetical == reference:
        return {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "受限括号昵称归一化后精确匹配成功。",
            "match_type": "exact_after_supported_parenthetical_alias",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": supported_parenthetical,
        }

    # 3. 未获准括号或新增否定/候选连接词直接失败关闭。
    if _has_parenthetical_group(reference) or _has_parenthetical_group(pred_text):
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "括号限定词未被参考答案或受限昵称规则支持。",
            "match_type": "parenthetical_qualifier_mismatch",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }
    if _has_unexpected_assertion_markers(reference, pred_text):
        return {
            "pass": False,
            "score": 0.5,
            "status": "ok",
            "reason": "预测新增否定或候选连接标记。",
            "match_type": "conflicting_assertion",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 4. 单值 KV 必须作为一个完整记录匹配，失败后禁止回落。
    if ":" in reference:
        if _kv_substring_match(reference, pred_text):
            return {
                "pass": True,
                "score": 1.0,
                "status": "ok",
                "reason": "单值 KV 完整记录匹配成功。",
                "match_type": "single_value_kv_match",
                "ref_text": reference,
                "pred_text": pred_text,
                "pred_text_cleaned": pred_text_cleaned,
            }
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "单值 KV 的键、字段数或值不匹配。",
            "match_type": "single_value_kv_no_match",
            "ref_text": reference,
            "pred_text": pred_text,
            "pred_text_cleaned": pred_text_cleaned,
        }

    # 5. 多关键词全部匹配（附加逗号 precision 检查）。
    if _keyword_match(reference, pred_text):
        pred_items = [
            x.strip() for x in re.split(r'[,，、]', pred_text) if x.strip()
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

    # 6. 包含匹配：降级为低置信度警告，不作为独立通过条件。
    if _contains_match(reference, pred_text):
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

    # 7. 全部不匹配。
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


def _evaluate_declared_match_mode(
    task_data: Dict,
    raw_prediction: Optional[str],
    interval_policy: Optional[IntervalPolicy] = None,
) -> Optional[Dict]:
    """执行任务显式声明的匹配契约并对未知模式失败关闭。

    功能：在 legacy 后缀剥离之前处理 ``exact``、``numeric`` 和
    ``keyed_numeric_set``；所有模式支持 accepted_answers。非空未知模式或字段
    类型错误返回 evaluator_error，禁止静默降级到松匹配链。
    输入参数：task_data 为已加载任务字典；raw_prediction 为最终答案标签内容或
    无标签时的完整输出；interval_policy 为可选任务级误差区间契约。
    输出返回值：已声明模式时返回完整评价结果；完全未声明时返回 None。
    """
    mode = str(task_data.get("answer_match_mode") or "").strip().lower()
    if not mode:
        return None
    supported_modes = {
        "exact",
        "strict_exact",
        "numeric",
        "keyed_numeric_set",
        "ordered_structured",
    }
    if mode not in supported_modes:
        return {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": f"未知 answer_match_mode: {mode}",
            "match_type": "unsupported_answer_match_mode",
            "ref_text": _canonical_exact_answer(task_data.get("answer")),
            "pred_text": _canonical_exact_answer(raw_prediction),
        }

    raw_reference = task_data.get("answer")
    raw_candidates: List[str] = [str(raw_reference or "")]
    aliases = task_data.get("accepted_answers") or []
    if not isinstance(aliases, (list, tuple)):
        return {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": "accepted_answers 必须是字符串列表。",
            "match_type": "invalid_accepted_answers_config",
            "ref_text": _canonical_exact_answer(raw_reference),
            "pred_text": _canonical_exact_answer(raw_prediction),
        }
    for alias in aliases:
        alias_text = str(alias or "")
        if alias_text and alias_text not in raw_candidates:
            raw_candidates.append(alias_text)

    if mode == "ordered_structured":
        prediction = _canonical_exact_answer(raw_prediction)
        references = [_canonical_exact_answer(item) for item in raw_candidates]
        parsed_references = [_parse_structured_answer(item) for item in references]
        if any(not is_structured for is_structured, _ in parsed_references):
            return {
                "pass": None,
                "score": None,
                "status": "evaluator_error",
                "reason": "ordered_structured 的参考答案或别名必须含至少两个分号分项。",
                "match_type": "invalid_ordered_structured_reference",
                "ref_text": references[0],
                "pred_text": prediction,
            }

        primary_result: Optional[Dict] = None
        for index, reference in enumerate(references):
            result = _evaluate_ordered_structured_candidate(
                reference,
                prediction,
                interval_policy,
            )
            if index == 0:
                primary_result = result
            if not result.get("pass"):
                continue
            if index > 0:
                result["match_type"] = "ordered_structured_via_alias"
                result["matched_alias"] = reference
                result["ref_text"] = references[0]
            return result
        return primary_result

    if mode in {"exact", "strict_exact"}:
        prediction = _canonical_exact_answer(raw_prediction)
        references = [_canonical_exact_answer(item) for item in raw_candidates]
        for index, candidate in enumerate(references):
            if prediction != candidate:
                continue
            match_type = "strict_exact" if index == 0 else "strict_exact_via_alias"
            result = {
                "pass": True,
                "score": 1.0,
                "status": "ok",
                "reason": "严格 canonical 精确匹配成功。",
                "match_type": match_type,
                "ref_text": references[0],
                "pred_text": prediction,
            }
            if index > 0:
                result["matched_alias"] = candidate
            return result
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "严格 canonical 精确匹配失败。",
            "match_type": "strict_exact_no_match",
            "ref_text": references[0],
            "pred_text": prediction,
        }

    if mode == "keyed_numeric_set":
        prediction_text = _canonical_exact_answer(raw_prediction)
        prediction_sets = _parse_keyed_numeric_sets(prediction_text)
        reference_sets = [
            _parse_keyed_numeric_sets(_canonical_exact_answer(item))
            for item in raw_candidates
        ]
        if any(item is None for item in reference_sets):
            return {
                "pass": None,
                "score": None,
                "status": "evaluator_error",
                "reason": "keyed_numeric_set 的参考答案或别名格式无效。",
                "match_type": "invalid_keyed_numeric_set_reference",
                "ref_text": _canonical_exact_answer(raw_candidates[0]),
                "pred_text": prediction_text,
            }
        for index, candidate in enumerate(reference_sets):
            if prediction_sets is None or prediction_sets != candidate:
                continue
            result = {
                "pass": True,
                "score": 1.0,
                "status": "ok",
                "reason": "键及其无序数字集合精确匹配。",
                "match_type": (
                    "keyed_numeric_set"
                    if index == 0
                    else "keyed_numeric_set_via_alias"
                ),
                "ref_text": _canonical_exact_answer(raw_candidates[0]),
                "pred_text": prediction_text,
            }
            if index > 0:
                result["matched_alias"] = _canonical_exact_answer(raw_candidates[index])
            return result
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "键、数字集合或唯一性不匹配。",
            "match_type": "keyed_numeric_set_no_match",
            "ref_text": _canonical_exact_answer(raw_candidates[0]),
            "pred_text": prediction_text,
        }

    prediction = _canonical_exact_answer(raw_prediction)
    references = [_canonical_exact_answer(item) for item in raw_candidates]
    accepted_units = task_data.get("accepted_units") or []
    if not isinstance(accepted_units, (list, tuple)):
        return {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": "accepted_units 必须是字符串列表。",
            "match_type": "invalid_accepted_units_config",
            "ref_text": references[0],
            "pred_text": prediction,
        }
    normalized_units = [str(unit) for unit in accepted_units if str(unit).strip()]
    if _parse_numeric_literal(references[0]) is None:
        return {
            "pass": None,
            "score": None,
            "status": "evaluator_error",
            "reason": "numeric 模式的主参考答案必须是完整数字字面量。",
            "match_type": "invalid_numeric_reference",
            "ref_text": references[0],
            "pred_text": prediction,
        }
    for index, candidate in enumerate(references):
        candidate_number = _parse_numeric_literal(candidate)
        numeric_match = (
            candidate_number is not None
            and _numeric_context_match(candidate, prediction, normalized_units)
        )
        text_alias_match = index > 0 and candidate_number is None and prediction == candidate
        if not numeric_match and not text_alias_match:
            continue
        if index == 0:
            match_type = "numeric_value"
        elif numeric_match:
            match_type = "numeric_value_via_alias"
        else:
            match_type = "numeric_text_alias"
        result = {
            "pass": True,
            "score": 1.0,
            "status": "ok",
            "reason": "numeric 数值或显式文字别名精确匹配。",
            "match_type": match_type,
            "ref_text": references[0],
            "pred_text": prediction,
        }
        if index > 0:
            result["matched_alias"] = candidate
        return result
    return {
        "pass": False,
        "score": 0.0,
        "status": "ok",
        "reason": "numeric 模式要求唯一、无冲突且数值相等的答案。",
        "match_type": "numeric_value_mismatch",
        "ref_text": references[0],
        "pred_text": prediction,
    }


def evaluate(task: Union[Dict, str], agent_answer: Optional[str]) -> Dict:
    """评估只读 FileSearch 与共享 QA 任务答案。

    功能：优先抽取最终 answer 标签和处理显式 exact/numeric/keyed 契约；未声明
    模式的结构化答案使用一对一 F1，legacy 单值依次执行精确、关键词及低置信
    包含检查。skip_eval 与配置错误分别返回 skip/evaluator_error。
    输入参数：task 为任务字典或 JSON 路径；agent_answer 为 Agent 原始输出或
    None。
    输出返回值：包含 pass、score、status、reason、match_type 及必要诊断统计的
    评价结果字典。
    """
    task_data = _load_task(task)

    # skip_eval: true 任务直接跳过
    if task_data.get("skip_eval"):
        return build_skip_evaluation(task_data)

    interval_policy, interval_policy_error = _parse_interval_policy(task_data)
    if interval_policy_error is not None:
        return interval_policy_error

    raw_reference = task_data.get("answer")
    reference = _normalize_answer(raw_reference)
    extracted = _extract_answer_tag(agent_answer)
    raw_prediction = extracted if extracted is not None else agent_answer
    pred_text = _normalize_answer(raw_prediction)

    # 检测中止/错误信息
    _raw_answer = (raw_prediction or "").strip().lower()
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

    # 仅最终选中答案完整等于哨兵时才视为主动弃答。
    if _is_abstention_answer(raw_prediction):
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "Agent 主动放弃作答 (INSUFFICIENT_EVIDENCE)",
            "match_type": "agent_abstained",
            "ref_text": reference,
            "pred_text": _raw_answer,
        }

    declared_mode_result = _evaluate_declared_match_mode(
        task_data,
        raw_prediction,
        interval_policy,
    )
    if declared_mode_result is not None:
        return declared_mode_result

    # 显式模式已优先执行；以下才进入会剥离文件后缀的 legacy 空答案逻辑。
    if reference == "":
        if not pred_text:
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

    if not pred_text:
        return {
            "pass": False,
            "score": 0.0,
            "status": "ok",
            "reason": "预测答案为空。",
            "ref_text": reference,
            "pred_text": pred_text,
        }

    pred_text_cleaned = _remove_parentheses_content(pred_text)

    # ---- 候选循环（primary + accepted_answers 别名）----
    # 每个候选按自身形态（结构化多值/单值）独立匹配，任一 pass=True 即返回；
    # 全部失败回落到 primary 的结果。
    # 设计目的：缓解 ref 用文件名（如 paper3）但 GUI/Plan Agent 抽取语义内容
    # （如论文标题）的假阴性，evaluator 需要兼容同一信息的多种合法表达。
    # 多值标答（含分号）的别名同样生效：别名整体需 F1=1.0 才算命中。
    aliases = task_data.get("accepted_answers") or []
    candidates: List[str] = [reference]
    for alias in aliases:
        norm = _normalize_answer(alias)
        if norm and norm not in candidates:
            candidates.append(norm)

    primary_result: Optional[Dict] = None
    for idx, ref_candidate in enumerate(candidates):
        result = _evaluate_one_candidate(
            ref_candidate,
            pred_text,
            pred_text_cleaned,
            interval_policy,
        )
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


def _evaluate_one_candidate(
    reference: str,
    pred_text: str,
    pred_text_cleaned: str,
    interval_policy: Optional[IntervalPolicy] = None,
) -> Dict:
    """对单个标答候选按自身形态执行完整匹配。

    功能：分号多值候选使用 reference-aware 拆分和一对一最大匹配计算 F1；
    单值候选交给失败关闭的单值匹配链。accepted_answers 由外层逐一调用本函数。
    输入参数：reference 为归一化主答案或别名；pred_text 为归一化预测；
    pred_text_cleaned 仅供单值结果诊断；interval_policy 为可选任务级误差
    区间契约。结构化匹配不使用被删括号的文本。
    输出返回值：完整评价字典；结构化结果额外包含 matched、precision、recall、
    f1 和分项计数。
    """
    # ---- 结构化多值（候选含分号）：按分号切分做 F1，F1=1.0 才 pass ----
    is_structured, parts = _parse_structured_answer(reference)
    if is_structured:
        # reference-aware 拆分保留合法实体中的 and，同时暴露夹带的错误候选。
        pred_parts, disjunction_conflicts = _split_structured_prediction(
            pred_text,
            parts,
        )
        if not pred_parts:
            pred_parts = [pred_text]

        # 一对一最大匹配同时决定 recall 与 precision，重复项不能重复消费标答。
        matched_count = _maximum_structured_matches(
            parts,
            pred_parts,
            interval_policy,
        )
        recall = matched_count / len(parts) if parts else 0.0
        pred_matched = matched_count
        precision_denominator = len(pred_parts) + disjunction_conflicts
        precision = (
            pred_matched / precision_denominator
            if precision_denominator
            else 0.0
        )

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
                f"precision={pred_matched}/{precision_denominator}, F1={f1:.4f}。"
            ),
            "match_type": "structured_f1_match" if is_pass else "structured_f1_partial",
            "matched": matched_count,
            "total": len(parts),
            "pred_parts_count": precision_denominator,
            "disjunction_conflicts": disjunction_conflicts,
            "pred_matched": pred_matched,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "ref_text": reference,
            "pred_text": pred_text,
        }

    # ---- 单值候选 ----
    return _match_single_reference(reference, pred_text, pred_text_cleaned)
