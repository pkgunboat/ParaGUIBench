"""32 个 Operation eval-rules 任务的不可变规则闭包。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


OPERATION_PROTOCOL_ID = "paraguibench.operation.eval-rules.v1"


@dataclass(frozen=True, slots=True)
class OperationTaskRule:
    """绑定一个 canonical 任务的完整 eval-rules 身份。

    输入参数：
        task_id：ParaGUIBench canonical 任务 ID。
        rule_set_sha256：完整、有序 ``eval_rules`` canonical JSON 摘要。
        check_ids：规则中显式检查函数名的有序序列。
        artifact_kinds：评价所需的 artifact 家族闭集。
    输出返回值：
        不可变任务规则；不包含 gold、文件路径或任务正文。
    """

    task_id: str
    rule_set_sha256: str
    check_ids: tuple[str, ...]
    artifact_kinds: tuple[str, ...]

    @property
    def rule_id(self) -> str:
        """返回公开且不含 gold 的版本化规则身份。

        输入参数：
            无；使用当前对象的 canonical task ID。
        输出返回值：
            ``paraguibench.operation.rule.<task_id>.v1`` 字符串。
        """

        return f"paraguibench.operation.rule.{self.task_id}.v1"


def _task(
    task_id: str,
    digest: str,
    checks: tuple[str, ...],
    kinds: tuple[str, ...],
) -> OperationTaskRule:
    """构造一条内部任务规则并校验固定身份格式。

    输入参数：
        task_id/digest/checks/kinds：任务 ID、规则摘要、检查序列和 artifact 家族。
    输出返回值：
        通过基本闭包校验的 ``OperationTaskRule``。
    """

    if len(digest) != 64 or not checks or not kinds:
        raise RuntimeError("Operation 规则目录定义无效")
    return OperationTaskRule(task_id, digest, checks, kinds)


_TASKS = (
    _task(
        "Operation-FileOperate-BatchOperationExcel-001",
        "7ba4387d043eeeae413d7c35a6f101783864c2c68cf3ce32d57ec3c17dd65663",
        ("check_batchexcel001_annual_sum",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-002",
        "8b47e536c77e915622d4f79aebf12727471a0047be6007ae225e2743efaa92c0",
        ("check_batchexcel002_header_bold", "check_batchexcel002_range_right_align"),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-003",
        "2eb56fc32fd9964afc7f30e46e7adbbbca6a168cdc072de6d3316ca23bb776aa",
        ("check_sort_order",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-004",
        "b4ccc6729af6380a851113a874ee7725c0cdbaf002dc32d4fb03079165f0ce05",
        ("check_cell_contains_string",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-005",
        "85095e19de51d538b44d8b9be8f9146bad1b8833915b201f564bcb21c7744fb1",
        ("check_values_scaled_from_source",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-006",
        "4dd0a1469fbecdc9fd3e75dbda14e89ecf1ce76930b2e1461771d691cbb94ac1",
        ("check_negative_values_colored",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-007",
        "3d3637d6a00fe0e5de6b8a2d4417e1f8767dde42d38c11ba6fb8265df4c7f7d6",
        ("check_sorted_copies_preserve_rows",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationExcel-009",
        "84fa6d19a7490d7e0adb3c21d6200820ec8de68cb993031eb705babe66278045",
        ("check_sequential_numbers",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationPPT-001",
        "aa0b13ebb169a67e2e513afda02d22feb5be94d70605775c56d383c6b2f6d336",
        ("check_slide_transition",),
        ("pptx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationPPT-002",
        "89299c1f6a81900eeef9b8719ccd4749faf7fee421559d0b73b32cebe01efba3",
        ("check_batchppt002_bounds_overlap",),
        ("pptx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-001",
        "6103a7ca8e8c68d310e0fe90cca6422a233015280235922e61ed98778c320f67",
        ("check_heading_hierarchy",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-002",
        "a60c3ff5ea873da994050bfbb66e4976e34ea6753f1bfd02c180d2634dfbd39e",
        ("check_batchword002_tab_indent", "check_max_consecutive_blank_lines"),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-003",
        "67d0fd311be4744e3de9cb4a7f58421f20d9176eb73df8680604ccdeaed97053",
        ("check_highlighted_words_capitalized",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-004",
        "d767ba1e4e0435867d9839f3fa50a45fb6afc22b5114bcbe80c1f6434fa35626",
        (
            "check_misspelled_words_highlighted",
            "check_misspelled_words_highlighted",
            "check_misspelled_words_highlighted",
            "check_misspelled_words_highlighted",
            "check_misspelled_words_highlighted",
        ),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-005",
        "42a40fe933cf760cfbfab601490e0b2e09a5001745ceb201cc5a4c94ca055757",
        ("check_has_toc",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-006",
        "e16b1cf865ac3f1d6de1a97079e9df9eb4205153dbda04587234bb435227beb0",
        ("check_table_contains_expected_values",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-007",
        "aa9df68a8d3dc5bacac0e2cf26f76bfc2b8506632ec06f19695620b76cd89fcd",
        ("check_vowels_colored_red",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-008",
        "cce5379cff1de951bd889b1c328deb0dcbf0516e6a1d1e53b1ef6d96dd9ea0d1",
        ("check_font_name",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-009",
        "ed52a2b2c36d9acdeb311bfb35930fbd3cf4b4cccb1d4b10c8acbb55fc0f3b14",
        ("check_line_spacing",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-010",
        "055e07b07f7e0ed14c9edd98617f412c5183769f30d2c65eb3dce00dfcc48c01",
        ("check_image_name_matches_doc",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-011",
        "04b531d9a39e9bbee96321910c71def5d6dfaba50f4e7df5095f3a49b5b53f0b",
        ("check_heading_palette_and_references",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-BatchOperationWord-012",
        "820b6ad7d13ed6ed4d00e3368ba97b303b76de7cfe4f1439947c0f3b5bb8266b",
        ("check_uppercase_words_have_parentheses",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-001",
        "44e86fe50a887dd3cc0bc6224fa473a28a65dbb2f6d27561c4e5fc6b5a7e6081",
        ("check_html_files_for_xlsx",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-003",
        "dfc80df353362a973f46032ccec3cc18ee7f35863ef742c423d7184ed6c8fde4",
        ("check_combinationdocs003_source_table_insert",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-005",
        "6eb08fc94126018bab2be5b1c9674f72526ea2cbb9ad01989c8a941cab420a5a",
        ("check_named_files_exist",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-006",
        "ac40e56a157c7472240e5cd2d4bed0c705c476210e0757d383a655ccbca95f4b",
        ("check_docx_has_hyperlink",),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-007",
        "9f843194fe26ed2d768b9aa442b438c5dc75dea6188a6f8b37408e3fcf540637",
        ("check_files_in_same_folder",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-CombinationDocs-008",
        "6ae54533cbdbe8341da64d87730fb62269557a0103542be4b7cf4589eedef8b6",
        ("check_named_files_exist",),
        ("directory",),
    ),
    _task(
        "Operation-FileOperate-SearchAndWrite-002",
        "713eb9822f9ed0f9129fa12f14086157730c0bd7b600ecd5e47709caa57b0f29",
        ("check_multi_cell_values", "check_cells_filled"),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-SearchAndWrite-004",
        "17395aba1543a92c7f2359179b43abbfc618f33231c420fa807d688f904b3ae1",
        ("check_multi_cell_values",),
        ("xlsx",),
    ),
    _task(
        "Operation-FileOperate-SearchAndWrite-006",
        "8347b8fc1b73ebf46a769fac79674323d0e76058e3fe31eba56e377b02ac2c7a",
        ("check_headings_have_body", "check_docx_word_count"),
        ("docx",),
    ),
    _task(
        "Operation-FileOperate-SearchAndWrite-007",
        "f479853597b4b47065c072c640e81b0d03a45b4b4c486b6ef49fb902ef5f3db7",
        ("check_multi_cell_values", "check_cells_filled"),
        ("xlsx",),
    ),
)

OPERATION_TASK_RULES: Mapping[str, OperationTaskRule] = MappingProxyType(
    {rule.task_id: rule for rule in _TASKS}
)

if len(OPERATION_TASK_RULES) != 32:
    raise RuntimeError("Operation 规则目录必须精确包含 32 个任务")


__all__ = [
    "OPERATION_PROTOCOL_ID",
    "OPERATION_TASK_RULES",
    "OperationTaskRule",
]
