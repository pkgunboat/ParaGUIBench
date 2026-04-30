"""
Word 文档（.docx）属性检查原语。

每个函数接收文件路径和参数字典，返回标准化结果：
    {"pass": bool, "score": float 0.0~1.0, "reason": str}

依赖: python-docx
"""

import logging
import re
from typing import Any, Dict, List, Optional

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Pt, Cm, Emu

logger = logging.getLogger("eval.operation_checks.docx")


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _load_document(file_path: str) -> Optional[Document]:
    """
    安全加载 docx 文件。

    输入:
        file_path: docx 文件路径
    输出:
        Document 对象；加载失败返回 None
    """
    try:
        return Document(file_path)
    except Exception as exc:
        logger.error("无法打开 docx 文件 %s: %s", file_path, exc)
        return None


def _ok(reason: str = "通过") -> Dict[str, Any]:
    """构造通过结果。"""
    return {"pass": True, "score": 1.0, "reason": reason}


def _fail(reason: str) -> Dict[str, Any]:
    """构造失败结果。"""
    return {"pass": False, "score": 0.0, "reason": reason}


def _partial(score: float, reason: str) -> Dict[str, Any]:
    """构造部分通过结果。严格阈值：仅当 score 等于 1.0 才算 pass。"""
    return {"pass": score >= 1.0 - 1e-9, "score": round(score, 4), "reason": reason}


def _config_error(reason: str) -> Dict[str, Any]:
    """评价器配置错误（缺参数等）：score=-1 哨兵，由上层冒泡为 evaluator_error。"""
    return {"pass": False, "score": -1.0, "status": "evaluator_error", "reason": reason}


# ------------------------------------------------------------------
# 检查函数
# ------------------------------------------------------------------

def check_max_consecutive_blank_lines(file_path: str, params: dict) -> dict:
    """
    检查文档中连续空段落数是否超过允许的最大值。

    输入:
        file_path: docx 文件路径
        params:
            max_allowed (int): 允许的最大连续空行数，默认 1
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    max_allowed = params.get("max_allowed", 1)
    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    max_found = 0
    current_streak = 0
    violations = []

    for i, para in enumerate(doc.paragraphs):
        if para.text.strip() == "":
            current_streak += 1
            if current_streak > max_found:
                max_found = current_streak
            if current_streak > max_allowed:
                violations.append(i)
        else:
            current_streak = 0

    if max_found <= max_allowed:
        return _ok(f"最大连续空行 {max_found} ≤ {max_allowed}")

    return _fail(
        f"发现连续空行 {max_found} 行（允许 {max_allowed}），"
        f"共 {len(violations)} 处违规"
    )


def check_font_name(file_path: str, params: dict) -> dict:
    """
    检查文档中所有 run 的字体是否为指定字体名称。

    输入:
        file_path: docx 文件路径
        params:
            font_name (str): 期望的字体名称，如 "Times New Roman"
            threshold (float): 符合比例阈值，默认 0.9（90% 以上的 run 字体正确即通过）
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    expected_font = params.get("font_name", "")
    threshold = params.get("threshold", 0.9)

    if not expected_font:
        return _config_error("参数缺少 font_name")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_runs = 0
    matched_runs = 0

    for para in doc.paragraphs:
        for run in para.runs:
            if not run.text.strip():
                continue
            total_runs += 1
            run_font = run.font.name
            # 有些 run 继承段落/样式字体，font.name 可能为 None
            if run_font and run_font.lower() == expected_font.lower():
                matched_runs += 1
            elif run_font is None:
                # 尝试从段落样式获取字体
                style_font = para.style.font.name if para.style and para.style.font else None
                if style_font and style_font.lower() == expected_font.lower():
                    matched_runs += 1

    if total_runs == 0:
        return _ok("文档无可检查的文本 run")

    ratio = matched_runs / total_runs
    if ratio >= threshold:
        return _ok(f"字体匹配率 {ratio:.1%}（{matched_runs}/{total_runs}）")

    return _partial(
        ratio,
        f"字体匹配率 {ratio:.1%}（{matched_runs}/{total_runs}），期望 ≥ {threshold:.0%}"
    )


def check_line_spacing(file_path: str, params: dict) -> dict:
    """
    检查文档段落的行距是否为指定值。

    输入:
        file_path: docx 文件路径
        params:
            spacing (float): 期望的行距倍数，如 2.0 表示双倍行距
            threshold (float): 符合比例阈值，默认 0.9
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    expected_spacing = params.get("spacing")
    threshold = params.get("threshold", 0.9)

    if expected_spacing is None:
        return _config_error("参数缺少 spacing")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_paras = 0
    matched_paras = 0

    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        total_paras += 1

        pf = para.paragraph_format
        if pf.line_spacing is None:
            continue

        # line_spacing 可能是 float（倍数）或 Pt 值（固定磅值）
        if pf.line_spacing_rule in (
            WD_LINE_SPACING.MULTIPLE,
            WD_LINE_SPACING.DOUBLE,
            WD_LINE_SPACING.ONE_POINT_FIVE,
        ):
            actual = float(pf.line_spacing)
        elif isinstance(pf.line_spacing, (int, float)):
            actual = float(pf.line_spacing)
        else:
            # Emu/Pt 固定值，转换为近似倍数（基准 12pt）
            try:
                actual = float(pf.line_spacing) / Pt(12)
            except Exception:
                continue

        if abs(actual - expected_spacing) < 0.05:
            matched_paras += 1

    if total_paras == 0:
        return _ok("文档无可检查的非空段落")

    ratio = matched_paras / total_paras
    if ratio >= threshold:
        return _ok(f"行距匹配率 {ratio:.1%}（{matched_paras}/{total_paras}）")

    return _partial(
        ratio,
        f"行距匹配率 {ratio:.1%}（{matched_paras}/{total_paras}），"
        f"期望 {expected_spacing} 倍行距，阈值 ≥ {threshold:.0%}"
    )


def check_heading_hierarchy(file_path: str, params: dict) -> dict:
    """
    检查文档的标题层级是否符合预期规则。

    输入:
        file_path: docx 文件路径
        params:
            rules (list[dict]): 标题规则列表，每项格式：
                {
                    "pattern": str,          # 正则表达式匹配标题文本
                    "expected_style": str,   # 期望的样式名称，如 "Heading 1"
                }
            threshold (float): 符合比例阈值，默认 0.8
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    rules = params.get("rules", [])
    threshold = params.get("threshold", 0.8)

    if not rules:
        return _config_error("参数缺少 rules")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_checks = 0
    matched_checks = 0
    mismatches = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        for rule in rules:
            pattern = rule.get("pattern", "")
            expected_style = rule.get("expected_style", "")
            if not pattern or not expected_style:
                continue

            if re.search(pattern, text):
                total_checks += 1
                actual_style = para.style.name if para.style else ""
                if actual_style == expected_style:
                    matched_checks += 1
                else:
                    mismatches.append({
                        "text": text[:50],
                        "expected": expected_style,
                        "actual": actual_style,
                    })

    if total_checks == 0:
        return _ok("未匹配到需要检查的标题")

    ratio = matched_checks / total_checks
    if ratio >= threshold:
        return _ok(
            f"标题层级匹配率 {ratio:.1%}（{matched_checks}/{total_checks}）"
        )

    mismatch_summary = "; ".join(
        f"'{m['text']}' 期望 {m['expected']} 实际 {m['actual']}"
        for m in mismatches[:5]
    )
    return _partial(
        ratio,
        f"标题层级匹配率 {ratio:.1%}，不匹配: {mismatch_summary}"
    )


def check_has_toc(file_path: str, params: dict) -> dict:
    """
    检查文档是否包含目录（Table of Contents）。

    通过检测 TOC 域代码或特定样式（TOC Heading / TOC 1-9）来判断。

    输入:
        file_path: docx 文件路径
        params: {}（无额外参数）
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    # 方法 1：检查 XML 中是否存在 TOC 域代码
    from lxml import etree
    body = doc.element.body
    # w:fldChar + w:instrText 中包含 "TOC" 字样
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    instr_texts = body.findall(".//w:instrText", namespaces)
    for instr in instr_texts:
        if instr.text and "TOC" in instr.text.upper():
            return _ok("检测到 TOC 域代码")

    # 方法 2：检查段落样式是否包含 TOC 相关样式
    toc_style_prefixes = ("toc ", "toc heading", "目录")
    for para in doc.paragraphs:
        if para.style and para.style.name:
            style_lower = para.style.name.lower()
            if any(style_lower.startswith(prefix) for prefix in toc_style_prefixes):
                return _ok(f"检测到 TOC 样式: {para.style.name}")

    # 方法 3：检查 SDT（结构化文档标签）中的 TOC
    sdt_elements = body.findall(".//w:sdt", namespaces)
    for sdt in sdt_elements:
        sdt_pr = sdt.find("w:sdtPr", namespaces)
        if sdt_pr is not None:
            doc_part = sdt_pr.find("w:docPartGallery", namespaces)
            if doc_part is not None:
                val = doc_part.get(f"{{{namespaces['w']}}}val", "")
                if "toc" in val.lower() or "table of contents" in val.lower():
                    return _ok("检测到 SDT 目录结构")

    return _fail("未检测到目录（TOC）")


def check_first_line_indent(file_path: str, params: dict) -> dict:
    """
    检查文档正文段落是否有首行缩进。

    输入:
        file_path: docx 文件路径
        params:
            min_indent_cm (float): 最小首行缩进值（厘米），默认 0.5
            threshold (float): 符合比例阈值，默认 0.8
            skip_styles (list[str]): 跳过检查的样式名称列表（如标题样式）
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    min_indent_cm = params.get("min_indent_cm", 0.5)
    threshold = params.get("threshold", 0.8)
    skip_styles = set(params.get("skip_styles", [
        "Heading 1", "Heading 2", "Heading 3", "Heading 4",
        "Title", "Subtitle", "TOC Heading",
    ]))

    min_indent_emu = Cm(min_indent_cm)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_paras = 0
    indented_paras = 0

    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        if para.style and para.style.name in skip_styles:
            continue

        total_paras += 1
        pf = para.paragraph_format
        first_indent = pf.first_line_indent

        if first_indent is not None and first_indent >= min_indent_emu:
            indented_paras += 1

    if total_paras == 0:
        return _ok("文档无需检查首行缩进的正文段落")

    ratio = indented_paras / total_paras
    if ratio >= threshold:
        return _ok(f"首行缩进率 {ratio:.1%}（{indented_paras}/{total_paras}）")

    return _partial(
        ratio,
        f"首行缩进率 {ratio:.1%}（{indented_paras}/{total_paras}），"
        f"期望 ≥ {threshold:.0%}"
    )


# ------------------------------------------------------------------
# 任务专用检查函数
# ------------------------------------------------------------------

def check_batchword002_tab_indent(file_path: str, params: dict) -> dict:
    """
    BatchoperationWord-002 专用：检查每个正文段落首行缩进是否为一个 tab 字符。

    判断方式（满足任一即视为有 tab 缩进）：
      1. 段落文本以 '\\t' 开头
      2. 段落中第一个 run 的文本以 '\\t' 开头

    输入:
        file_path: docx 文件路径
        params: 忽略
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    skip_styles = {
        "Heading 1", "Heading 2", "Heading 3", "Heading 4",
        "Title", "Subtitle", "TOC Heading",
    }

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_paras = 0
    tab_paras = 0

    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        if para.style and para.style.name in skip_styles:
            continue

        total_paras += 1

        # 检查段落文本或首个 run 是否以 tab 开头
        if para.text.startswith("\t"):
            tab_paras += 1
        elif para.runs and para.runs[0].text.startswith("\t"):
            tab_paras += 1

    if total_paras == 0:
        return _ok("文档无需检查的正文段落")

    ratio = tab_paras / total_paras
    if ratio >= 0.8:
        return _ok(f"Tab 缩进率 {ratio:.1%}（{tab_paras}/{total_paras}）")

    return _partial(
        ratio,
        f"Tab 缩进率 {ratio:.1%}（{tab_paras}/{total_paras}）"
    )


# ------------------------------------------------------------------
# 扩展检查函数
# ------------------------------------------------------------------

def check_heading_style_exists(file_path: str, params: dict) -> dict:
    """
    检查文档是否存在指定样式的标题。

    输入:
        file_path: docx 文件路径
        params:
            style_name (str): 期望的标题样式名称，如 "Heading 1"
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    style_name = params.get("style_name", "")

    if not style_name:
        return _config_error("参数缺少 style_name")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    found = False
    count = 0
    for para in doc.paragraphs:
        if para.style and para.style.name == style_name:
            found = True
            count += 1

    if found:
        return _ok(f"存在 {count} 个 '{style_name}' 样式段落")

    return _fail(f"未找到 '{style_name}' 样式的段落")


def check_has_table(file_path: str, params: dict) -> dict:
    """
    检查文档是否存在表格。

    输入:
        file_path: docx 文件路径
        params:
            min_tables (int): 最少表格数量，默认 1
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    min_tables = params.get("min_tables", 1)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    table_count = len(doc.tables)

    if table_count >= min_tables:
        return _ok(f"文档包含 {table_count} 个表格")

    return _fail(f"文档仅有 {table_count} 个表格（期望至少 {min_tables} 个）")


def check_vowels_colored_red(file_path: str, params: dict) -> dict:
    """
    检查文档中元音字母（aeiouAEIOU）是否被标记为红色。

    输入:
        file_path: docx 文件路径
        params:
            threshold (float): 符合比例阈值，默认 0.8
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    threshold = params.get("threshold", 0.8)
    vowels = set("aeiouAEIOU")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_vowels = 0
    red_vowels = 0

    for para in doc.paragraphs:
        for run in para.runs:
            text = run.text
            if not text:
                continue
            for char in text:
                if char in vowels:
                    total_vowels += 1
                    if run.font.color and run.font.color.rgb:
                        color = run.font.color.rgb
                        if hasattr(color, "r") and color.r == 255 and color.g == 0 and color.b == 0:
                            red_vowels += 1
                        elif str(color).upper() in ("FFFF0000", "FF0000", "RED"):
                            red_vowels += 1

    if total_vowels == 0:
        return _ok("文档无元音字母可检查")

    ratio = red_vowels / total_vowels
    if ratio >= threshold:
        return _ok(f"元音字母红色标记率 {ratio:.1%}（{red_vowels}/{total_vowels}）")

    return _partial(ratio, f"元音红色率 {ratio:.1%}（{red_vowels}/{total_vowels}）")


def check_uppercase_words_have_parentheses(file_path: str, params: dict) -> dict:
    """
    检查文档中纯大写的单词（如 MAC）后是否都有括号。

    输入:
        file_path: docx 文件路径
        params:
            threshold (float): 符合比例阈值，默认 0.9
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    threshold = params.get("threshold", 0.9)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    import re
    uppercase_word_pattern = re.compile(r'\b[A-Z]{2,}\b')

    total_uppercase = 0
    with_parentheses = 0
    details = []

    full_text = ""
    for para in doc.paragraphs:
        full_text += para.text + " "

    matches = uppercase_word_pattern.findall(full_text)
    total_uppercase = len(matches)

    for para in doc.paragraphs:
        para_text = para.text
        for match in uppercase_word_pattern.finditer(para_text):
            word = match.group()
            end_pos = match.end()
            if end_pos < len(para_text) and para_text[end_pos] == '(':
                with_parentheses += 1
            else:
                details.append(word)

    if total_uppercase == 0:
        return _ok("文档无纯大写单词可检查")

    ratio = with_parentheses / total_uppercase
    if ratio >= threshold:
        return _ok(f"大写单词括号率 {ratio:.1%}（{with_parentheses}/{total_uppercase}）")

    return _partial(ratio, f"大写词括号率 {ratio:.1%}（{with_parentheses}/{total_uppercase}），无括号: {', '.join(details[:3])}")


def check_highlighted_words_capitalized(file_path: str, params: dict) -> dict:
    """
    检查黄色高亮的词是否有大写字母开头。

    输入:
        file_path: docx 文件路径
        params:
            highlight_color (str): 高亮颜色，默认 "FFFF00"（黄色）
            threshold (float): 符合比例阈值，默认 0.8
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    highlight_color = params.get("highlight_color", "FFFF00").upper()
    threshold = params.get("threshold", 0.8)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total_highlighted = 0
    capitalized_count = 0

    for para in doc.paragraphs:
        for run in para.runs:
            text = run.text
            if not text:
                continue
            if run.font.highlight_color:
                hl = str(run.font.highlight_color).upper()
                if highlight_color in hl or "YELLOW" in hl or run.font.highlight_color == 7:
                    total_highlighted += 1
                    if text and text[0].isupper():
                        capitalized_count += 1

    if total_highlighted == 0:
        return _fail("未找到黄色高亮文本")

    ratio = capitalized_count / total_highlighted
    if ratio >= threshold:
        return _ok(f"高亮词大写开头率 {ratio:.1%}（{capitalized_count}/{total_highlighted}）")

    return _partial(ratio, f"大写开头率 {ratio:.1%}（{capitalized_count}/{total_highlighted}）")


def check_misspelled_words_highlighted(file_path: str, params: dict) -> dict:
    """
    检查特定拼写错误的词是否被黄色高亮标记。

    输入:
        file_path: docx 文件路径
        params:
            expected_highlights (dict): 期望被高亮的词及其所在文档的映射
                例如: {"intrenational": "travel", "conmference": "travel"}
            highlight_color (str): 高亮颜色，默认 "FFFF00"
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    expected_highlights = params.get("expected_highlights", {})
    highlight_color = params.get("highlight_color", "FFFF00").upper()

    if not expected_highlights:
        return _config_error("参数缺少 expected_highlights")

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    total = len(expected_highlights)
    matched = 0
    details = []

    for para in doc.paragraphs:
        for run in para.runs:
            text = run.text.lower()
            if not text:
                continue
            for misspelled, doc_key in expected_highlights.items():
                if misspelled.lower() in text:
                    if run.font.highlight_color:
                        hl = str(run.font.highlight_color).upper()
                        if highlight_color in hl or "YELLOW" in hl or run.font.highlight_color == 7:
                            matched += 1
                            details.append(f"'{misspelled}' 在 {doc_key} 中已高亮")
                        else:
                            details.append(f"'{misspelled}' 未高亮")
                    else:
                        details.append(f"'{misspelled}' 未高亮")

    if matched == total:
        return _ok(f"全部 {total} 个错误词都已黄色高亮")

    ratio = matched / total
    return _partial(ratio, f"{matched}/{total} 错误词已高亮: {', '.join(details[:5])}")


def check_heading_colors_different(file_path: str, params: dict) -> dict:
    """
    检查文档中不同标题的颜色是否互不相同。

    输入:
        file_path: docx 文件路径
        params:
            heading_styles (list[str]): 需要检查的标题样式列表
            threshold (float): 符合比例阈值，默认 0.8
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    heading_styles = params.get("heading_styles", ["Heading 1", "Heading 2", "Heading 3"])
    threshold = params.get("threshold", 0.8)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    heading_colors = []

    for para in doc.paragraphs:
        if para.style and para.style.name in heading_styles:
            for run in para.runs:
                if run.text.strip():
                    if run.font.color and run.font.color.rgb:
                        color = str(run.font.color.rgb).upper()
                        heading_colors.append((para.style.name, run.text[:20], color))
                    else:
                        heading_colors.append((para.style.name, run.text[:20], "无颜色"))

    if len(heading_colors) < 2:
        return _ok("标题数量不足，无法比较颜色")

    colors_only = [c[2] for c in heading_colors]
    unique_colors = set(colors_only)

    ratio = len(unique_colors) / len(colors_only) if colors_only else 0.0

    if len(unique_colors) >= len(colors_only) * threshold:
        return _ok(f"全部 {len(unique_colors)} 个标题颜色都不同")

    return _partial(ratio, f"{len(unique_colors)} 种颜色 / {len(colors_only)} 个标题")


def check_image_name_matches_doc(file_path: str, params: dict) -> dict:
    """
    检查文档中插入的图片名和文档名是否一致。

    输入:
        file_path: docx 文件路径
        params:
            threshold (float): 符合比例阈值，默认 0.8
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    import os as _os
    threshold = params.get("threshold", 0.8)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    doc_name = _os.path.splitext(_os.path.basename(file_path))[0].lower()

    total_images = 0
    matched_images = 0

    for shape in doc.inline_shapes:
        total_images += 1
        blip = shape._inline.graphic.graphicData.pic.blipFill.blip
        rId = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if rId and rId in doc.part.rels:
            image_part = doc.part.rels[rId].target_part
            image_name = _os.path.basename(image_part.partname).lower()
            image_base = _os.path.splitext(image_name)[0]
            if doc_name in image_base or image_base in doc_name:
                matched_images += 1

    if total_images == 0:
        return _fail("文档中无内联图片")

    ratio = matched_images / total_images
    if ratio >= threshold:
        return _ok(f"图片名匹配率 {ratio:.1%}（{matched_images}/{total_images}）")

    return _partial(ratio, f"匹配率 {ratio:.1%}（{matched_images}/{total_images}）")


def check_docx_word_count(file_path: str, params: dict) -> dict:
    """
    检查Word文档的字数是否大于指定值。

    输入:
        file_path: docx 文件路径
        params:
            min_words (int): 最少字数要求，默认 100
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    min_words = params.get("min_words", 100)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    word_count = 0
    for para in doc.paragraphs:
        words = para.text.split()
        word_count += len(words)

    if word_count >= min_words:
        return _ok(f"文档字数 {word_count} >= {min_words}")

    ratio = word_count / min_words
    return _partial(ratio, f"文档字数 {word_count} < {min_words}")


def check_docx_has_hyperlink(file_path: str, params: dict) -> dict:
    """
    检查Word文档中是否存在超链接。

    输入:
        file_path: docx 文件路径
        params:
            threshold (float): 符合比例阈值，默认 0.5
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    threshold = params.get("threshold", 0.5)

    doc = _load_document(file_path)
    if doc is None:
        return _fail(f"无法打开文件: {file_path}")

    from lxml import etree
    body = doc.element.body
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    hyperlinks = body.findall(".//w:hyperlink", namespaces)
    link_count = len(hyperlinks)

    if link_count == 0:
        return _fail("文档中不存在超链接")

    # 统计总段落数
    total_paras = len([p for p in doc.paragraphs if p.text.strip()])
    if total_paras == 0:
        return _ok(f"文档无段落，但包含 {link_count} 个超链接")

    ratio = min(1.0, link_count / total_paras)
    if ratio >= threshold:
        return _ok(f"文档包含 {link_count} 个超链接")

    return _partial(ratio, f"仅 {link_count} 个超链接")
