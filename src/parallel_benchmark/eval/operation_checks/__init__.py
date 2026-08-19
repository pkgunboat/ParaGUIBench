"""
Operation 任务属性检查原语包。

提供 docx / xlsx / pptx / file 四类原子检查函数，供 operation_evaluator.py 调度。
每个检查函数遵循统一签名：

    def check_xxx(file_path: str, params: dict) -> dict:
        返回 {"pass": bool, "score": float, "reason": str}
"""

from eval.operation_checks.docx_checks import (
    check_max_consecutive_blank_lines,
    check_font_name,
    check_line_spacing,
    check_heading_hierarchy,
    check_has_toc,
    check_first_line_indent,
    check_batchword002_tab_indent,
    # 扩展函数
    check_heading_style_exists,
    check_has_table,
    check_vowels_colored_red,
    check_uppercase_words_have_parentheses,
    check_highlighted_words_capitalized,
    check_misspelled_words_highlighted,
    check_heading_colors_different,
    check_image_name_matches_doc,
    check_docx_word_count,
)
from eval.operation_checks.xlsx_checks import (
    check_cell_value,
    check_header_bold,
    check_column_alignment,
    check_sort_order,
    check_has_sum_row,
    check_batchexcel001_annual_sum,
    check_batchexcel002_header_bold,
    check_batchexcel002_range_right_align,
    # 扩展函数
    check_cell_contains_string,
    check_values_are_decimals,
    check_negative_values_colored,
    check_sorted_columns_exist,
    check_no_na_values,
    check_sequential_numbers,
    check_multi_cell_values,
    check_cells_filled,
)
from eval.operation_checks.pptx_checks import (
    check_slide_transition,
    check_text_not_overflow,
    check_batchppt002_bounds_overlap,
    # 扩展函数
    check_ppt_has_images_or_tables,
    check_ppt_slide_has_number,
)
from eval.operation_checks.file_checks import (
    check_files_exist,
    check_files_in_same_folder,
    check_html_files_for_xlsx,
    check_named_files_exist,
)

__all__ = [
    # docx — 通用
    "check_max_consecutive_blank_lines",
    "check_font_name",
    "check_line_spacing",
    "check_heading_hierarchy",
    "check_has_toc",
    "check_first_line_indent",
    # docx — 任务专用
    "check_batchword002_tab_indent",
    # docx — 扩展
    "check_heading_style_exists",
    "check_has_table",
    "check_vowels_colored_red",
    "check_uppercase_words_have_parentheses",
    "check_highlighted_words_capitalized",
    "check_misspelled_words_highlighted",
    "check_heading_colors_different",
    "check_image_name_matches_doc",
    "check_docx_word_count",
    "check_docx_has_hyperlink",
    # xlsx — 通用
    "check_cell_value",
    "check_header_bold",
    "check_column_alignment",
    "check_sort_order",
    "check_has_sum_row",
    # xlsx — 任务专用
    "check_batchexcel001_annual_sum",
    "check_batchexcel002_header_bold",
    "check_batchexcel002_range_right_align",
    # xlsx — 扩展
    "check_cell_contains_string",
    "check_values_are_decimals",
    "check_negative_values_colored",
    "check_sorted_columns_exist",
    "check_no_na_values",
    "check_sequential_numbers",
    "check_multi_cell_values",
    "check_cells_filled",
    # pptx — 通用
    "check_slide_transition",
    "check_text_not_overflow",
    # pptx — 任务专用
    "check_batchppt002_bounds_overlap",
    # pptx — 扩展
    "check_ppt_has_images_or_tables",
    "check_ppt_slide_has_number",
    # file — 通用
    "check_files_exist",
    "check_files_in_same_folder",
    "check_html_files_for_xlsx",
    "check_named_files_exist",
]
