"""SearchAndWrite-008 评价器、bridge 与 runtime 共用的唯一机器合同。

本模块只保存已由固定 input/gold 真实字节复验的不可变
身份；不读取文件、Agent final text 或 RunStore，也不向 guest 暴露
gold 路径、摘要或值。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Literal, TypeAlias


SEARCHWRITE_XLSX_TASK_ID = "Operation-FileOperate-SearchAndWrite-008"
SEARCHWRITE_XLSX_TASK_UID = "65a4848d-b4b2-4173-8308-a0213fdafbd0"
SEARCHWRITE_XLSX_PROTOCOL_ID = "paraguibench.operation.searchwrite-xlsx.v1"
SEARCHWRITE_BASELINE_PROJECTION_PROTOCOL_ID = (
    "paraguibench.operation.searchwrite-baseline.v6"
)
SEARCHWRITE_CELL_MATCH_PROTOCOL_ID = "paraguibench.operation.searchwrite-cell-match.v1"
SEARCHWRITE_MACHINE_IDENTITY_VERSION = "paraguibench.searchwrite008.machine.v1"
SEARCHWRITE_INPUT_MANIFEST_SHA256 = (
    "8c16214b1d0be1ad9e936a0032adbcfb8025ff612f2db4f57f67c6f181440f4d"
)
SEARCHWRITE_GOLD_MANIFEST_SHA256 = (
    "be4da59bf9721894663421bff550b48269fa23104fd0ceb81aaf54371da1607b"
)
# 该摘要还同时固定两份 manifest 的完整条目、来源、协议和
# 下方两份文档合同；runtime 使用严格 JSON 投影重算后比较。
SEARCHWRITE_MACHINE_IDENTITY_SHA256 = (
    "a07a8540c1b2e9d809dac8e794d07667f5a0a8f2056f33232ab83dfbea275e51"
)

SearchWriteExpectedKind: TypeAlias = Literal["integer", "number", "text"]
SearchWriteExpectedValue: TypeAlias = str | int | float
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DOCUMENT_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,63})")
_RELATIVE_XLSX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.xlsx")
_CELL_COORDINATE_PATTERN = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}")


@dataclass(frozen=True, slots=True, repr=False)
class SearchWriteExpectedCellContract:
    """保存一个目标单元格的有序类型语义。

    输入参数：
        coordinate：首工作表中的固定 A1 坐标。
        value_kind：显式 ``integer``、``number`` 或 ``text`` 类型。
        expected_value：只由 host evaluator 在内存中使用的固定期望值。
    输出返回值：
        不可变单元格合同；自定义表示不包含坐标或期望值。
    """

    coordinate: str
    value_kind: SearchWriteExpectedKind
    expected_value: SearchWriteExpectedValue

    def __post_init__(self) -> None:
        """在导入阶段失败关闭坐标、显式类型与值的配对。

        输入参数：无；读取已构造的冻结字段。
        输出返回值：配对有效时返回 ``None``。
        异常：
            ValueError：坐标、类型或值不在闭集中。
        """

        if (
            not isinstance(self.coordinate, str)
            or _CELL_COORDINATE_PATTERN.fullmatch(self.coordinate) is None
        ):
            raise ValueError("SearchWrite 单元格坐标无效")
        if self.value_kind == "text":
            valid_value = type(self.expected_value) is str and bool(self.expected_value)
        elif self.value_kind == "integer":
            valid_value = type(self.expected_value) is int
        elif self.value_kind == "number":
            valid_value = type(self.expected_value) in {int, float} and math.isfinite(
                float(self.expected_value)
            )
        else:
            valid_value = False
        if not valid_value:
            raise ValueError("SearchWrite 单元格类型或值无效")

    def __repr__(self) -> str:
        """返回不含坐标或期望值的脱敏表示。

        输入参数：无。
        输出返回值：仅显示显式值类型的稳定字符串。
        """

        return f"SearchWriteExpectedCellContract(value_kind={self.value_kind!r})"


@dataclass(frozen=True, slots=True, repr=False)
class SearchWriteDocumentContract:
    """保存一份工作簿的路径、语义投影与九格子集。

    输入参数：
        relative_path：input 与 gold manifest 共用的固定相对路径。
        document_id：evaluator 使用的脱敏逻辑文档身份。
        baseline_sha256：排除目标格内容后的 v6 语义投影摘要。
        expected_cells：按协议顺序保存的目标单元格 tuple。
    输出返回值：
        评价器、artifact bridge 和 runtime identity 共用的不可变合同。
    """

    relative_path: str
    document_id: str
    baseline_sha256: str
    expected_cells: tuple[SearchWriteExpectedCellContract, ...]

    def __post_init__(self) -> None:
        """验证文档身份、摘要和目标坐标闭集。

        输入参数：无；读取已构造的冻结字段。
        输出返回值：完整且坐标唯一时返回 ``None``。
        异常：
            ValueError：路径、文档 ID、摘要或坐标闭集无效。
        """

        if (
            not isinstance(self.relative_path, str)
            or _RELATIVE_XLSX_PATTERN.fullmatch(self.relative_path) is None
            or not isinstance(self.document_id, str)
            or _DOCUMENT_ID_PATTERN.fullmatch(self.document_id) is None
            or not isinstance(self.baseline_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.baseline_sha256) is None
            or not isinstance(self.expected_cells, tuple)
            or not self.expected_cells
            or any(
                not isinstance(cell, SearchWriteExpectedCellContract)
                for cell in self.expected_cells
            )
        ):
            raise ValueError("SearchWrite 文档合同无效")
        coordinates = tuple(cell.coordinate for cell in self.expected_cells)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("SearchWrite 文档坐标重复")

    @property
    def target_coordinates(self) -> tuple[str, ...]:
        """返回 bridge 允许读取的有序目标坐标。

        输入参数：无。
        输出返回值：从 ``expected_cells`` 单向派生的坐标 tuple。
        """

        return tuple(cell.coordinate for cell in self.expected_cells)

    def __repr__(self) -> str:
        """返回不含路径、摘要、坐标或值的脱敏表示。

        输入参数：无。
        输出返回值：仅显示目标单元格数的稳定字符串。
        """

        return (
            "SearchWriteDocumentContract("
            f"expected_cell_count={len(self.expected_cells)!r})"
        )


SEARCHWRITE_DOCUMENT_CONTRACTS: tuple[SearchWriteDocumentContract, ...] = (
    SearchWriteDocumentContract(
        relative_path="UK_Universities_Group1.xlsx",
        document_id="group-1",
        baseline_sha256=(
            "3e8f109167ba857fe9398fde8a5453afd4fb64ec2447f309118095385f33a258"
        ),
        expected_cells=(
            SearchWriteExpectedCellContract("C6", "integer", 2),
            SearchWriteExpectedCellContract("D6", "text", "London"),
            SearchWriteExpectedCellContract("B7", "integer", 1826),
            SearchWriteExpectedCellContract("D8", "text", "Edinburgh"),
        ),
    ),
    SearchWriteDocumentContract(
        relative_path="UK_Universities_Group2.xlsx",
        document_id="group-2",
        baseline_sha256=(
            "2c48b26c448dae4ee085a9e01cd7b1c73d7e06b0b16dd0c1e6f3c1fa9dc39f7f"
        ),
        expected_cells=(
            SearchWriteExpectedCellContract("D4", "text", "Manchester"),
            SearchWriteExpectedCellContract("B5", "integer", 1829),
            SearchWriteExpectedCellContract("C6", "integer", 45),
            SearchWriteExpectedCellContract("B8", "integer", 1965),
            SearchWriteExpectedCellContract("D8", "text", "Coventry"),
        ),
    ),
)


__all__ = [
    "SEARCHWRITE_BASELINE_PROJECTION_PROTOCOL_ID",
    "SEARCHWRITE_CELL_MATCH_PROTOCOL_ID",
    "SEARCHWRITE_DOCUMENT_CONTRACTS",
    "SEARCHWRITE_GOLD_MANIFEST_SHA256",
    "SEARCHWRITE_INPUT_MANIFEST_SHA256",
    "SEARCHWRITE_MACHINE_IDENTITY_SHA256",
    "SEARCHWRITE_MACHINE_IDENTITY_VERSION",
    "SEARCHWRITE_XLSX_PROTOCOL_ID",
    "SEARCHWRITE_XLSX_TASK_ID",
    "SEARCHWRITE_XLSX_TASK_UID",
    "SearchWriteDocumentContract",
    "SearchWriteExpectedCellContract",
    "SearchWriteExpectedKind",
    "SearchWriteExpectedValue",
]
