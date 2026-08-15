"""pipeline-implicit 正式固定资产清单与可信字节解析边界。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from typing import Any
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from paraguibench.evaluation.pipeline_implicit.searchwrite_contract import (
    SEARCHWRITE_XLSX_TASK_ID,
    SEARCHWRITE_XLSX_TASK_UID,
)


EXCEL008_TASK_ID = "Operation-FileOperate-BatchOperationExcel-008"
EXCEL008_TASK_UID = "1c73128f-a5ef-4a97-97ce-ef427d6d46b4"
EXCEL008_INPUT_MANIFEST_PATH = f"benchmark/assets/manifests/{EXCEL008_TASK_ID}.json"
EXCEL008_GOLD_MANIFEST_PATH = f"benchmark/gold/manifests/{EXCEL008_TASK_ID}.json"
COMBINATION002_TASK_ID = "Operation-FileOperate-CombinationDocs-002"
COMBINATION002_TASK_UID = "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8"
COMBINATION002_INPUT_MANIFEST_PATH = (
    f"benchmark/assets/manifests/{COMBINATION002_TASK_ID}.json"
)
COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH = (
    "benchmark/provenance/pipeline-implicit-known-negative/"
    f"{COMBINATION002_TASK_ID}.json"
)
PPT003_TASK_ID = "Operation-FileOperate-BatchOperationPPT-003"
PPT003_TASK_UID = "e544ee0f-90e6-43a4-9958-6b74e88d94a6"
PPT003_INPUT_MANIFEST_PATH = f"benchmark/assets/manifests/{PPT003_TASK_ID}.json"
PPT003_GOLD_MANIFEST_PATH = f"benchmark/gold/manifests/{PPT003_TASK_ID}.json"
SEARCHWRITE008_TASK_ID = SEARCHWRITE_XLSX_TASK_ID
SEARCHWRITE008_TASK_UID = SEARCHWRITE_XLSX_TASK_UID
SEARCHWRITE008_INPUT_MANIFEST_PATH = (
    f"benchmark/assets/manifests/{SEARCHWRITE008_TASK_ID}.json"
)
SEARCHWRITE008_GOLD_MANIFEST_PATH = (
    f"benchmark/gold/manifests/{SEARCHWRITE008_TASK_ID}.json"
)
_LEE_REPOSITORY = "leeLegendary/Parallel_benchmark"
_LEE_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
_LICENSE_EVIDENCE_REF = (
    "https://huggingface.co/datasets/leeLegendary/Parallel_benchmark"
)
_JPEG = "image/jpeg"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
_CONTENT_TYPES_ROOT = f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
_CONTENT_TYPES_DEFAULT = f"{{{_CONTENT_TYPES_NAMESPACE}}}Default"
_CONTENT_TYPES_OVERRIDE = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"

_EXCEL008_INPUT_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "KFC_Monthly_Data.xlsx",
        9_532,
        "3e21f4657d6fe68210e5f68ba5bad2db979dd47f5902b8be09114903fed00ead",
        _XLSX,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_535,
        "4c901ba683cff4c629eba5ca070b5d76684a827f2012145e3b8a09d477230761",
        _XLSX,
    ),
    (
        "Mixue_Monthly_Data.xlsx",
        5_866,
        "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
        _XLSX,
    ),
    (
        "PizzaHut_Monthly_Data.xlsx",
        9_535,
        "1fda0cabc98adc934b4314da8afb36bb38cb7681a49f3753a43384dda0f211c8",
        _XLSX,
    ),
    (
        "Subway_Monthly_Data.xlsx",
        9_527,
        "20ee0a872bd508276c9971122c12eaa510c8a1825bc44a94433261894892ba96",
        _XLSX,
    ),
)
_EXCEL008_GOLD_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "KFC_Monthly_Data.xlsx",
        7_313,
        "35d4144ed899fdbb14ccb07a99d18d042190027718a1467f852355e09491e60e",
        _XLSX,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        7_326,
        "db90397c5afcdcbfc280c18afd694d9802c05327b93f079cb34742d4ca398f04",
        _XLSX,
    ),
    (
        "Mixue_Monthly_Data.xlsx",
        5_866,
        "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
        _XLSX,
    ),
    (
        "PizzaHut_Monthly_Data.xlsx",
        7_326,
        "f6c67a77d417484174eede29b173774dc158d0f25229abc6f8db6fac8d00572b",
        _XLSX,
    ),
    (
        "Subway_Monthly_Data.xlsx",
        7_354,
        "322c97248f024c5cbac031736caae5b450e1c51c6ddb44e4f0f59398428dfa13",
        _XLSX,
    ),
)
_COMBINATION002_INPUT_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "McDonald_finacial_report.docx",
        14_351,
        "df1a15647946cba883e00cb1d0228f075b5e12e6b5deb02acb9c4f79a931515b",
        _DOCX,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_545,
        "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9",
        _XLSX,
    ),
    (
        "McDonalds_powerpoint_report.pptx",
        39_699,
        "a96a98ecba8bf648fae8357c35d31197d1594c063130737dd098a9c3ac1c712d",
        _PPTX,
    ),
)
_COMBINATION002_KNOWN_NEGATIVE_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "McDonald_finacial_report.docx",
        10_974,
        "8ae14dbe3e701e8671bfdd17b24e1b9e098cd42d0f08c2e9ea584908d21dd9fa",
        _DOCX,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_545,
        "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9",
        _XLSX,
    ),
    (
        "McDonalds_powerpoint_report.pptx",
        49_687,
        "963617e29c37f7b653f40d4616dd636d6f756d64dcb24afe4bb68e3a4447c635",
        _PPTX,
    ),
)

# 这些元组来自固定 Lee revision 的真实下载字节。图片只在这里保存一次；
# gold 中的分类副本通过 ``_CLASSIFIED_COPIES`` 复用同一字节身份，避免两份
# 手工哈希表发生漂移。
_SOURCE_IMAGES: tuple[tuple[str, int, str, str], ...] = (
    (
        "images/Unknown-1.jpeg",
        8_476,
        "920c257be076389b03fe784a05181d91d45f3f679b554d4717b5786880b8ccba",
        _JPEG,
    ),
    (
        "images/Unknown-10.jpeg",
        13_430,
        "0ab17ba065b88996a05bd6176a4626a16a02f1db4048fd6f92da83046f86b058",
        _JPEG,
    ),
    (
        "images/Unknown-11.jpeg",
        14_094,
        "d5dfa19f5270f6005b33eca0afe95509c9a5b5c317db26cdb4abc40a2d2fbbe4",
        _JPEG,
    ),
    (
        "images/Unknown-12.jpeg",
        9_698,
        "56691d1a2e16c1f02a69f6451ea0cf36800943b15cf1e3f8cf9ac5ba452fc343",
        _JPEG,
    ),
    (
        "images/Unknown-13.jpeg",
        12_825,
        "7b0c75780124245d509ab97373c52c90f9d337519babbaafab02cabb4ad6562d",
        _JPEG,
    ),
    (
        "images/Unknown-14.jpeg",
        11_521,
        "4a3f86e36920a19a5ee7922d24fe45c795b597f8bd4b8f1224c869c0a5abe7f8",
        _JPEG,
    ),
    (
        "images/Unknown-15.jpeg",
        8_269,
        "66a6116e2912dcaed8864256dc77826e50e5f9f104f41feb7b6460d0ac50981a",
        _JPEG,
    ),
    (
        "images/Unknown-2.jpeg",
        11_549,
        "67cc896c74124417543c367b28a136539a7e218f4c2249f12c86d7b8aa4e89ee",
        _JPEG,
    ),
    (
        "images/Unknown-3.jpeg",
        9_775,
        "98e6eb3d5cfe30a7918ddefea122353451639a515421d8234e2944424f9f8dd5",
        _JPEG,
    ),
    (
        "images/Unknown-4.jpeg",
        8_555,
        "87260c5b287cb41013b590b46360ee35ceb3e64a0fb1b3aa5db4c00d7c9d47ae",
        _JPEG,
    ),
    (
        "images/Unknown-5.jpeg",
        9_783,
        "dfcd32b378a7f9e585036373c5a24d4cb92bb0360d0714f4afb3dfd13ee6ebdf",
        _JPEG,
    ),
    (
        "images/Unknown-6.jpeg",
        6_007,
        "1b20d8c30349856b961b870391dfb4a50e19af652295fd9b24a00d7ae39a8593",
        _JPEG,
    ),
    (
        "images/Unknown-7.jpeg",
        15_961,
        "4ecc1ed717d25250dcb9ff12b92cb8361ad0d88d90f06d0fc3a606e1a0506485",
        _JPEG,
    ),
    (
        "images/Unknown-8.jpeg",
        15_828,
        "5eabea4b59197f829ed7fb12e8c5a220d87b12427baeb79551126cff69389032",
        _JPEG,
    ),
    (
        "images/Unknown-9.jpeg",
        11_562,
        "8f43a7206becfb714d8e978124fa3416e684a804a2d225d27d3795c15bb1b0f0",
        _JPEG,
    ),
    (
        "images/Unknown.jpeg",
        13_540,
        "df28be039df8fe12f6edba1654ba9a239cd463bfdba5189d4530d3de74a44d1d",
        _JPEG,
    ),
)
_PRESENTATIONS: tuple[tuple[str, int, str, str], ...] = (
    (
        "ppt1.pptx",
        37_888,
        "9482822e725c18c7aca918d244888353442d77ce9d0962c7652d6bb9eaf5b7fe",
        _PPTX,
    ),
    (
        "ppt2.pptx",
        36_341,
        "3ace91b9f45521a96857c94db36fa5a21d0f90f7401636c08b2d12d691166c05",
        _PPTX,
    ),
    (
        "ppt3.pptx",
        35_522,
        "2ad2b30684b8e8fe690b8b5d94b27878b41266b7b945870d30273effdb9c6cf0",
        _PPTX,
    ),
    (
        "ppt4.pptx",
        36_928,
        "0d5080eacc501d8c5e67e32ef1a8bc8535fedfb420df1e9d31b3ad961c12cdfc",
        _PPTX,
    ),
)
_CLASSIFIED_COPIES: tuple[tuple[str, str], ...] = (
    ("basketball/Unknown-1.jpeg", "images/Unknown-1.jpeg"),
    ("basketball/Unknown-2.jpeg", "images/Unknown-2.jpeg"),
    ("basketball/Unknown.jpeg", "images/Unknown.jpeg"),
    ("esport/Unknown-10.jpeg", "images/Unknown-10.jpeg"),
    ("esport/Unknown-11.jpeg", "images/Unknown-11.jpeg"),
    ("esport/Unknown-9.jpeg", "images/Unknown-9.jpeg"),
    ("soccer/Unknown-3.jpeg", "images/Unknown-3.jpeg"),
    ("soccer/Unknown-4.jpeg", "images/Unknown-4.jpeg"),
    ("soccer/Unknown-5.jpeg", "images/Unknown-5.jpeg"),
    ("soccer/Unknown-6.jpeg", "images/Unknown-6.jpeg"),
    ("volleyball/Unknown-7.jpeg", "images/Unknown-7.jpeg"),
    ("volleyball/Unknown-8.jpeg", "images/Unknown-8.jpeg"),
)
_SEARCHWRITE_INPUT_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "UK_Universities_Group1.xlsx",
        8_908,
        "df08dc5e24d04a9587c21154b363511e01bc2ec18e9411d179e29e9231188e27",
        _XLSX,
    ),
    (
        "UK_Universities_Group2.xlsx",
        8_900,
        "7936c66869e26be9e787e703e801c74b7034afd22f934ca3b166a3d4b021caaa",
        _XLSX,
    ),
)
_SEARCHWRITE_GOLD_FILES: tuple[tuple[str, int, str, str], ...] = (
    (
        "UK_Universities_Group1.xlsx",
        5_877,
        "0170c5dab6a6062c610517b297708ad496a8bfa53699915ad6c3ff3948bf81cd",
        _XLSX,
    ),
    (
        "UK_Universities_Group2.xlsx",
        5_895,
        "b19a72eb28ad9a55ed956247dd8fb97f59ec5ede751ece25ac963614631ef257",
        _XLSX,
    ),
)


class PipelineImplicitGoldManifestError(ValueError):
    """表示 pipeline-implicit 专属 gold manifest 不可信。"""

    code = "PIPELINE_IMPLICIT_GOLD_MANIFEST_INVALID"

    def __init__(self, detail: str | None = None) -> None:
        """构造不泄漏路径、摘要或 manifest 内容的固定异常。

        输入参数：
            detail：仅供调用点表达意图；不会进入公开异常文本。
        输出返回值：
            无；``str(error)`` 恒为固定 code。
        """

        del detail
        super().__init__(self.code)


class PipelineImplicitGoldIntegrityError(RuntimeError):
    """表示 gold 缓存闭集或真实字节未通过固定身份门禁。"""

    code = "PIPELINE_IMPLICIT_GOLD_INTEGRITY_INVALID"

    def __init__(self) -> None:
        """构造不泄漏路径、摘要或内容的固定完整性异常。

        输入参数：无。
        输出返回值：无；``str(error)`` 恒为固定 code。
        """

        super().__init__(self.code)


class PipelineImplicitKnownNegativeManifestError(ValueError):
    """表示 CombinationDocs-002 的 audit-only 负例清单不可信。"""

    code = "PIPELINE_IMPLICIT_KNOWN_NEGATIVE_MANIFEST_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、摘要、业务事实或 manifest 内容的固定异常。

        输入参数：无。
        输出返回值：无；异常文本只含稳定错误码。
        """

        super().__init__(self.code)


class PipelineImplicitKnownNegativeIntegrityError(RuntimeError):
    """表示 audit-only 负例缓存未通过固定闭集或字节身份门禁。"""

    code = "PIPELINE_IMPLICIT_KNOWN_NEGATIVE_INTEGRITY_INVALID"

    def __init__(self) -> None:
        """构造不泄漏负例路径、摘要或正文的固定完整性异常。

        输入参数：无。
        输出返回值：无；异常文本只含稳定错误码。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitGoldEntry:
    """保存一项已由严格 manifest loader 固定的字节身份。

    输入参数：
        path：资产根内的安全相对 POSIX 路径。
        size_bytes/sha256/media_type：真实下载字节的固定身份。
    输出返回值：
        不可变 entry；自定义表示不泄漏路径或摘要。
    """

    path: str
    size_bytes: int
    sha256: str
    media_type: str

    def __repr__(self) -> str:
        """返回只含资源量与 MIME 的脱敏调试表示。

        输入参数：无。
        输出返回值：不含路径或摘要的稳定字符串。
        """

        return (
            "PipelineImplicitGoldEntry("
            f"size_bytes={self.size_bytes!r}, media_type={self.media_type!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitGoldManifest:
    """保存一个已注册 pipeline-implicit 任务的正式 gold 资产闭集。

    输入参数：
        manifest_id/task_id/task_uid/manifest_role：固定清单身份。
        source_revision/distribution_policy：不可变来源与 download-only 策略。
        entries：按 UTF-8 路径字节序排列的完整条目 tuple。
    输出返回值：
        不可变 manifest；只可由严格 bytes loader 产生可信实例。
    """

    manifest_id: str
    task_id: str
    task_uid: str
    source_revision: str
    distribution_policy: str
    entries: tuple[PipelineImplicitGoldEntry, ...]

    def __repr__(self) -> str:
        """返回不含文件身份的脱敏 manifest 表示。

        输入参数：无。
        输出返回值：仅含 task、role 与条目计数的稳定字符串。
        """

        return (
            "PipelineImplicitGoldManifest("
            f"task_id={self.task_id!r}, "
            f"entry_count={len(self.entries)!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitKnownNegativeManifest:
    """保存 HF answer 的 audit-only known-negative 固定身份。

    输入参数：manifest/task/source/entries 固定下载字节；score 与原因码固定
        当前正式 evaluator 对该历史答案的失败结果。
    输出返回值：不可变审计合同；类型上不能传入正式 gold resolver。
    """

    manifest_id: str
    task_id: str
    task_uid: str
    source_revision: str
    distribution_policy: str
    expected_score: float
    expected_reason_codes: tuple[str, ...]
    entries: tuple[PipelineImplicitGoldEntry, ...]

    def __repr__(self) -> str:
        """返回不含文档身份、事实值、路径与摘要的脱敏表示。

        输入参数：无。
        输出返回值：仅含任务、条目数和固定失败标记的字符串。
        """

        return (
            "PipelineImplicitKnownNegativeManifest("
            f"task_id={self.task_id!r}, entry_count={len(self.entries)!r}, "
            "expected_pass=False)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedPipelineImplicitGoldFile:
    """保存经 manifest、闭集、SHA 与 MIME 同时核验的 gold 字节。

    输入参数：
        size_bytes/media_type：可公开的资源与媒体类型元数据。
        _path/_sha256/_payload：只允许 pipeline evaluator 内部短期使用的
            已核验身份与不可变字节；调试表示不会暴露。
    输出返回值：
        只能由 ``resolve_verified_pipeline_implicit_gold_bundle`` 形成的
        evaluator-only 文件对象。
    """

    size_bytes: int
    media_type: str
    _path: str
    _sha256: str
    _payload: bytes

    def read_for_pipeline(self) -> bytes:
        """返回已完成全部完整性门禁的不可变 gold bytes。

        输入参数：无。
        输出返回值：仅供 production pipeline parser/evaluator 使用的 bytes。
        """

        return self._payload

    def __repr__(self) -> str:
        """返回不含路径、摘要和内容的脱敏表示。

        输入参数：无。
        输出返回值：仅含大小与 MIME 的稳定字符串。
        """

        return (
            "VerifiedPipelineImplicitGoldFile("
            f"size_bytes={self.size_bytes!r}, media_type={self.media_type!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedPipelineImplicitGoldBundle:
    """保存 production bridge 可接受的任务专属完整 gold 闭集。

    输入参数：
        task_id：固定且已注册的 pipeline-implicit canonical ID。
        _files：全部通过原始 manifest bytes 与本地内容校验的稳定 tuple。
    输出返回值：
        可迭代 evaluator-only verified 文件的不可变 bundle。
    """

    task_id: str
    _files: tuple[VerifiedPipelineImplicitGoldFile, ...]

    @property
    def file_count(self) -> int:
        """返回 verified gold 常规文件数。

        输入参数：无。
        输出返回值：当前任务严格 manifest 闭集的非负文件数。
        """

        return len(self._files)

    @property
    def total_bytes(self) -> int:
        """返回 verified gold payload 总字节数。

        输入参数：无。
        输出返回值：所有成员大小之和。
        """

        return sum(item.size_bytes for item in self._files)

    def iter_files_for_pipeline(
        self,
    ) -> tuple[VerifiedPipelineImplicitGoldFile, ...]:
        """返回仅含已核验文件对象的不可变序列。

        输入参数：无。
        输出返回值：按 manifest UTF-8 路径字节序排列的 tuple。
        """

        return self._files

    def __repr__(self) -> str:
        """返回不含 gold 身份与内容的脱敏 bundle 表示。

        输入参数：无。
        输出返回值：仅含 task、文件数和总大小的稳定字符串。
        """

        return (
            "VerifiedPipelineImplicitGoldBundle("
            f"task_id={self.task_id!r}, file_count={self.file_count!r}, "
            f"total_bytes={self.total_bytes!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedPipelineImplicitKnownNegativeFile:
    """保存只允许离线审计读取的 known-negative 固定字节。

    输入参数：size/media 为资源元数据；其余私有字段由专属 resolver 形成。
    输出返回值：只暴露 ``read_for_audit``，不提供 production/gold 读取 seam。
    """

    size_bytes: int
    media_type: str
    _path: str
    _sha256: str
    _payload: bytes

    def read_for_audit(self) -> bytes:
        """返回已核验且仅供 known-negative 回归使用的不可变字节。

        输入参数：无。
        输出返回值：固定 HF answer 的 bytes；调用方不得上传或持久化。
        """

        return self._payload

    def __repr__(self) -> str:
        """返回不含路径、摘要或内容的审计文件表示。

        输入参数：无。
        输出返回值：只含大小和 MIME 的稳定字符串。
        """

        return (
            "VerifiedPipelineImplicitKnownNegativeFile("
            f"size_bytes={self.size_bytes!r}, media_type={self.media_type!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedPipelineImplicitKnownNegativeBundle:
    """保存与 production gold 类型不可互换的 audit-only 三文件闭集。"""

    task_id: str
    _files: tuple[VerifiedPipelineImplicitKnownNegativeFile, ...]

    @property
    def file_count(self) -> int:
        """返回负例闭集文件数。

        输入参数：无。
        输出返回值：固定非负整数。
        """

        return len(self._files)

    def iter_files_for_audit(
        self,
    ) -> tuple[VerifiedPipelineImplicitKnownNegativeFile, ...]:
        """返回只允许审计代码消费的不可变文件序列。

        输入参数：无。
        输出返回值：按 manifest 路径顺序排列的 tuple。
        """

        return self._files

    def __repr__(self) -> str:
        """返回不含业务事实、路径、摘要或 payload 的脱敏表示。

        输入参数：无。
        输出返回值：仅含任务与文件数的稳定字符串。
        """

        return (
            "VerifiedPipelineImplicitKnownNegativeBundle("
            f"task_id={self.task_id!r}, file_count={self.file_count!r})"
        )


def build_ppt003_asset_manifest_documents(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """构造 PPT-003 的 input/gold 正式 download-only 清单。

    输入参数：
        repo_root：包含 canonical task 的 ParaGUIBench 仓库根。
    输出返回值：
        两个仓库相对 manifest 路径到 JSON object 的映射；input 固定
        20 项，gold 固定 32 项且保留全部 16 个 source-copy。
    异常：
        PipelineImplicitGoldManifestError：仓库根、canonical task ID
        或 task UID 与固定证据不一致。
    """

    return build_pipeline_implicit_asset_manifest_documents(
        repo_root,
        task_id=PPT003_TASK_ID,
    )


def build_excel008_asset_manifest_documents(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """构造 Excel-008 的 input/gold 正式 download-only 清单。

    输入参数：
        repo_root：包含 canonical task 的 ParaGUIBench 仓库根。
    输出返回值：
        两个仓库相对 manifest 路径到 JSON object 的映射；input
        与 gold 均精确包含五份 XLSX，但保留不同字节身份。
    异常：
        PipelineImplicitGoldManifestError：canonical task ID/UID 或闭集漂移。
    """

    return build_pipeline_implicit_asset_manifest_documents(
        repo_root,
        task_id=EXCEL008_TASK_ID,
    )


def build_combination002_asset_manifest_documents(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """构造 CombinationDocs-002 input 与 audit-only 负例清单。

    输入参数：
        repo_root：包含 canonical task 的 ParaGUIBench 仓库根。
    输出返回值：
        input 与 known-negative 各包含一份 DOCX、XLSX 和 PPTX；后者显式
        禁止作为 pass oracle，只记录正式 evaluator 的 2/3 失败回归。
    异常：
        PipelineImplicitGoldManifestError：canonical ID/UID 或闭集漂移。
    """

    return build_pipeline_implicit_asset_manifest_documents(
        repo_root,
        task_id=COMBINATION002_TASK_ID,
    )


def build_searchwrite008_asset_manifest_documents(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """构造 SearchWrite-008 的 input/gold 正式 download-only 清单。

    输入参数：
        repo_root：包含 canonical task 的 ParaGUIBench 仓库根。
    输出返回值：
        两个仓库相对 manifest 路径到 JSON object 的映射；input
        和 gold 均精确包含两个 XLSX，但保留不同的字节身份。
    异常：
        PipelineImplicitGoldManifestError：canonical task ID/UID 或固定
            资产闭集漂移。
    """

    return build_pipeline_implicit_asset_manifest_documents(
        repo_root,
        task_id=SEARCHWRITE008_TASK_ID,
    )


def build_pipeline_implicit_asset_manifest_documents(
    repo_root: Path,
    *,
    task_id: str,
) -> dict[str, dict[str, Any]]:
    """为已注册 pipeline-implicit 任务构造两种职责分离的清单。

    输入参数：
        repo_root：包含 canonical task 的仓库根。
        task_id：必须是本模块明确注册的 CombinationDocs-002、
            Excel-008、PPT-003 或 SearchWrite-008 canonical ID。
    输出返回值：
        input 通用 AssetManifest 与 task-specific reference 的两项映射；
        CombinationDocs-002 第二项是 audit-only known-negative，其余为 gold。
    异常：
        PipelineImplicitGoldManifestError：任务未注册、canonical 身份
            漂移或闭集数量不符合固定协议。
    """

    task_uid, input_manifest_path, reference_manifest_path = _task_identity(task_id)
    task = _load_canonical_identity(repo_root, task_id=task_id)
    if task.get("task_uid") != task_uid:
        raise PipelineImplicitGoldManifestError()
    input_files = _expected_files(task_id, "input")
    reference_role = "known_negative" if task_id == COMBINATION002_TASK_ID else "gold"
    reference_files = _expected_files(task_id, reference_role)
    expected_counts = {
        COMBINATION002_TASK_ID: (3, 3),
        EXCEL008_TASK_ID: (5, 5),
        PPT003_TASK_ID: (20, 32),
        SEARCHWRITE008_TASK_ID: (2, 2),
    }
    if (len(input_files), len(reference_files)) != expected_counts[task_id]:
        raise PipelineImplicitGoldManifestError()
    reference_document = (
        _build_combination002_known_negative_manifest(reference_files)
        if task_id == COMBINATION002_TASK_ID
        else _build_gold_manifest(task_id, task_uid, reference_files)
    )
    return {
        input_manifest_path: _build_input_manifest(
            task_id,
            task_uid,
            input_files,
        ),
        reference_manifest_path: reference_document,
    }


def load_verified_pipeline_implicit_gold_manifest(
    payload: bytes,
) -> PipelineImplicitGoldManifest:
    """从严格 JSON bytes 加载一份已注册任务的 gold 合同。

    输入参数：
        payload：仓库 manifest 的原始 UTF-8 bytes；不接受路径、字符串、
            bytearray 或已解析的可变 object。
    输出返回值：
        与固定 builder 在字段、顺序、来源、许可和全部 path→字节身份上
        完全一致的不可变 ``PipelineImplicitGoldManifest``。
    异常：
        PipelineImplicitGoldManifestError：大小、UTF-8、JSON、重复字段、
        非标准常量、未知/缺失字段或任一固定身份不一致。

    gold 中分类文件与 ``images`` source-copy 允许共享 SHA-256；loader
    校验的是固定 path→size/SHA/MIME 映射，而不是错误地要求 digest 全局唯一。
    """

    if not isinstance(payload, bytes) or not payload or len(payload) > 1_048_576:
        raise PipelineImplicitGoldManifestError()
    try:
        raw = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_non_standard_constant,
        )
    except PipelineImplicitGoldManifestError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise PipelineImplicitGoldManifestError() from None
    if not isinstance(raw, dict):
        raise PipelineImplicitGoldManifestError()
    if raw.get("manifest_role") != "gold":
        raise PipelineImplicitGoldManifestError()
    task_id = raw.get("task_id")
    task_uid, _, _ = _task_identity(task_id)
    expected = _build_gold_manifest(
        task_id,
        task_uid,
        _expected_files(task_id, "gold"),
    )
    if raw != expected:
        raise PipelineImplicitGoldManifestError()
    entries = tuple(
        PipelineImplicitGoldEntry(
            path=entry["path"],
            size_bytes=entry["size_bytes"],
            sha256=entry["sha256"],
            media_type=entry["media_type"],
        )
        for entry in raw["entries"]
    )
    return PipelineImplicitGoldManifest(
        manifest_id=raw["manifest_id"],
        task_id=raw["task_id"],
        task_uid=raw["task_uid"],
        source_revision=raw["source"]["revision"],
        distribution_policy=raw["distribution_policy"],
        entries=entries,
    )


def resolve_verified_pipeline_implicit_gold_bundle(
    manifest_payload: bytes,
    cache_root: Path,
) -> VerifiedPipelineImplicitGoldBundle:
    """把原始 manifest bytes 与本地 gold 闭集解析为可信 bundle。

    输入参数：
        manifest_payload：必须通过专属 strict bytes loader 的仓库 manifest；
            不接受已解析 object 或通用 OSWorld gold 类型。
        cache_root：预先下载的任务专属私有 gold 根；本函数不访问网络、
            不创建或修改缓存。
    输出返回值：
        每个成员均通过路径闭集、nofollow 常规文件、size、SHA-256 与
        JPEG/DOCX/PPTX/XLSX 内容类型检查的
        ``VerifiedPipelineImplicitGoldBundle``。
    异常：
        PipelineImplicitGoldManifestError：manifest bytes 不可信。
        PipelineImplicitGoldIntegrityError：缓存缺失、多余、符号链接、
            非常规文件、内容或真实媒体类型不一致。

    该窄接口保证后续 production bridge 只能接收 verified bytes，且
    不读取 Agent final text。
    """

    manifest = load_verified_pipeline_implicit_gold_manifest(manifest_payload)
    if not isinstance(cache_root, Path):
        raise PipelineImplicitGoldIntegrityError()
    root_descriptor = _open_gold_root_nofollow(cache_root)
    try:
        observed_paths = _enumerate_regular_gold_paths(root_descriptor)
        expected_paths = {entry.path for entry in manifest.entries}
        if observed_paths != expected_paths:
            raise PipelineImplicitGoldIntegrityError()

        verified_files: list[VerifiedPipelineImplicitGoldFile] = []
        for entry in manifest.entries:
            payload = _read_gold_file_nofollow(
                root_descriptor,
                entry.path,
                expected_size=entry.size_bytes,
            )
            if hashlib.sha256(payload).hexdigest() != entry.sha256:
                raise PipelineImplicitGoldIntegrityError()
            _verify_gold_media_type(payload, entry.media_type)
            verified_files.append(
                VerifiedPipelineImplicitGoldFile(
                    size_bytes=entry.size_bytes,
                    media_type=entry.media_type,
                    _path=entry.path,
                    _sha256=entry.sha256,
                    _payload=payload,
                )
            )
        if _enumerate_regular_gold_paths(root_descriptor) != observed_paths:
            raise PipelineImplicitGoldIntegrityError()
    finally:
        os.close(root_descriptor)
    return VerifiedPipelineImplicitGoldBundle(
        task_id=manifest.task_id,
        _files=tuple(verified_files),
    )


def load_pipeline_implicit_known_negative_manifest(
    payload: bytes,
) -> PipelineImplicitKnownNegativeManifest:
    """严格加载 CombinationDocs-002 的 audit-only known-negative 清单。

    输入参数：payload 为仓库内专属 manifest 原始 bytes。
    输出返回值：与确定性 builder 逐字段一致、且类型上不可作为 gold 的
        ``PipelineImplicitKnownNegativeManifest``。
    异常：PipelineImplicitKnownNegativeManifestError：JSON、角色、来源、
        失败结论或任一固定字节身份不一致。
    """

    if not isinstance(payload, bytes) or not payload or len(payload) > 1_048_576:
        raise PipelineImplicitKnownNegativeManifestError
    try:
        raw = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_non_standard_constant,
        )
        expected = _build_combination002_known_negative_manifest(
            _expected_files(COMBINATION002_TASK_ID, "known_negative")
        )
    except Exception:
        raise PipelineImplicitKnownNegativeManifestError from None
    if not isinstance(raw, dict) or raw != expected:
        raise PipelineImplicitKnownNegativeManifestError
    entries = tuple(
        PipelineImplicitGoldEntry(
            path=entry["path"],
            size_bytes=entry["size_bytes"],
            sha256=entry["sha256"],
            media_type=entry["media_type"],
        )
        for entry in raw["entries"]
    )
    evaluation = raw["expected_evaluation"]
    return PipelineImplicitKnownNegativeManifest(
        manifest_id=raw["manifest_id"],
        task_id=raw["task_id"],
        task_uid=raw["task_uid"],
        source_revision=raw["source"]["revision"],
        distribution_policy=raw["distribution_policy"],
        expected_score=float(evaluation["score"]),
        expected_reason_codes=tuple(evaluation["reason_codes"]),
        entries=entries,
    )


def resolve_pipeline_implicit_known_negative_bundle(
    manifest_payload: bytes,
    cache_root: Path,
) -> VerifiedPipelineImplicitKnownNegativeBundle:
    """从 held-dirfd 缓存解析 audit-only known-negative 字节闭集。

    输入参数：manifest_payload 为专属严格清单；cache_root 为预下载 HF
        answer 根。本函数不访问网络、不修改缓存，也不返回 gold 类型。
    输出返回值：只能通过 ``read_for_audit`` 消费的三文件不可变 bundle。
    异常：PipelineImplicitKnownNegativeManifestError 或
        PipelineImplicitKnownNegativeIntegrityError；均不泄漏路径或正文。
    """

    manifest = load_pipeline_implicit_known_negative_manifest(manifest_payload)
    if not isinstance(cache_root, Path):
        raise PipelineImplicitKnownNegativeIntegrityError
    try:
        root_descriptor = _open_gold_root_nofollow(cache_root)
        try:
            observed_paths = _enumerate_regular_gold_paths(root_descriptor)
            expected_paths = {entry.path for entry in manifest.entries}
            if observed_paths != expected_paths:
                raise PipelineImplicitKnownNegativeIntegrityError
            verified_files: list[VerifiedPipelineImplicitKnownNegativeFile] = []
            for entry in manifest.entries:
                item_payload = _read_gold_file_nofollow(
                    root_descriptor,
                    entry.path,
                    expected_size=entry.size_bytes,
                )
                if hashlib.sha256(item_payload).hexdigest() != entry.sha256:
                    raise PipelineImplicitKnownNegativeIntegrityError
                _verify_gold_media_type(item_payload, entry.media_type)
                verified_files.append(
                    VerifiedPipelineImplicitKnownNegativeFile(
                        size_bytes=entry.size_bytes,
                        media_type=entry.media_type,
                        _path=entry.path,
                        _sha256=entry.sha256,
                        _payload=item_payload,
                    )
                )
            if _enumerate_regular_gold_paths(root_descriptor) != observed_paths:
                raise PipelineImplicitKnownNegativeIntegrityError
        finally:
            os.close(root_descriptor)
    except PipelineImplicitKnownNegativeIntegrityError:
        raise
    except Exception:
        raise PipelineImplicitKnownNegativeIntegrityError from None
    return VerifiedPipelineImplicitKnownNegativeBundle(
        task_id=manifest.task_id,
        _files=tuple(verified_files),
    )


def _directory_open_flags() -> int:
    """返回 held directory descriptor 的最严格可用打开 flags。

    输入参数：无。
    输出返回值：
        ``O_RDONLY`` 加平台支持的 ``O_DIRECTORY/O_CLOEXEC/O_NOFOLLOW``。
    """

    flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _open_gold_root_nofollow(root: Path) -> int:
    """从文件系统锚点逐级 nofollow 打开并固定 gold 根。

    输入参数：
        root：调用方配置的 cache 根，可为相对或绝对 ``Path``。
    输出返回值：
        调用方拥有、固定最终目录 inode 的只读 descriptor。
    异常：
        PipelineImplicitGoldIntegrityError：任一祖先是 symlink、非目录、
        不存在或无法安全打开。
    """

    absolute = Path(os.path.abspath(os.fspath(root)))
    flags = _directory_open_flags()
    try:
        descriptor = os.open(absolute.anchor, flags)
    except OSError:
        raise PipelineImplicitGoldIntegrityError() from None
    try:
        for part in absolute.parts[1:]:
            try:
                child_descriptor = os.open(
                    part,
                    flags,
                    dir_fd=descriptor,
                )
            except OSError:
                raise PipelineImplicitGoldIntegrityError() from None
            os.close(descriptor)
            descriptor = child_descriptor
            try:
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise PipelineImplicitGoldIntegrityError()
            except OSError:
                raise PipelineImplicitGoldIntegrityError() from None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _enumerate_regular_gold_paths(root_descriptor: int) -> set[str]:
    """从 held root fd 枚举闭集并拒绝 symlink 与路径碰撞。

    输入参数：
        root_descriptor：由 ``_open_gold_root_nofollow`` 固定的目录 fd。
    输出返回值：
        全部单链接常规文件的 POSIX 相对路径集合。
    异常：
        PipelineImplicitGoldIntegrityError：任一节点不可信或遍历失败。
    """

    paths: set[str] = set()
    portable_keys: set[str] = set()

    def walk(directory_descriptor: int, prefix: tuple[str, ...]) -> None:
        """递归枚举一个 held directory descriptor。

        输入参数：
            directory_descriptor：当前已固定目录 fd。
            prefix：当前目录相对 gold 根的路径分量。
        输出返回值：无；结果累积到外层闭集。
        """

        try:
            names = sorted(
                os.listdir(directory_descriptor),
                key=lambda value: value.encode("utf-8"),
            )
        except (OSError, UnicodeError):
            raise PipelineImplicitGoldIntegrityError() from None
        for name in names:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise PipelineImplicitGoldIntegrityError() from None
            if stat.S_ISLNK(metadata.st_mode):
                raise PipelineImplicitGoldIntegrityError()
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child_descriptor = os.open(
                        name,
                        _directory_open_flags(),
                        dir_fd=directory_descriptor,
                    )
                except OSError:
                    raise PipelineImplicitGoldIntegrityError() from None
                try:
                    walk(child_descriptor, (*prefix, name))
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PipelineImplicitGoldIntegrityError()
            relative_path = "/".join((*prefix, name))
            portable_key = unicodedata.normalize(
                "NFC",
                relative_path,
            ).casefold()
            if relative_path in paths or portable_key in portable_keys:
                raise PipelineImplicitGoldIntegrityError()
            paths.add(relative_path)
            portable_keys.add(portable_key)

    walk(root_descriptor, ())
    return paths


def _read_gold_file_nofollow(
    root_descriptor: int,
    relative_path: str,
    *,
    expected_size: int,
) -> bytes:
    """通过 nofollow descriptor 有界读取一个固定 gold 文件。

    输入参数：
        root_descriptor：整个 batch 生命周期持有的 gold 根 descriptor。
        relative_path：strict manifest 固定的 POSIX 相对路径。
        expected_size：manifest 固定的正整数大小。
    输出返回值：
        descriptor 前后身份稳定、长度精确的不可变 bytes。
    异常：
        PipelineImplicitGoldIntegrityError：打开、类型、hardlink、大小、
        读取或前后 inode 身份不一致。
    """

    try:
        directory_descriptor = os.dup(root_descriptor)
    except OSError:
        raise PipelineImplicitGoldIntegrityError() from None
    try:
        for part in relative_path.split("/")[:-1]:
            try:
                child_descriptor = os.open(
                    part,
                    _directory_open_flags(),
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise PipelineImplicitGoldIntegrityError() from None
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                relative_path.split("/")[-1],
                flags,
                dir_fd=directory_descriptor,
            )
        except OSError:
            raise PipelineImplicitGoldIntegrityError() from None
    finally:
        os.close(directory_descriptor)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != expected_size
        ):
            raise PipelineImplicitGoldIntegrityError()
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if (
            len(payload) != expected_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
        ):
            raise PipelineImplicitGoldIntegrityError()
        return payload
    except PipelineImplicitGoldIntegrityError:
        raise
    except OSError:
        raise PipelineImplicitGoldIntegrityError() from None
    finally:
        os.close(descriptor)


def _verify_gold_media_type(payload: bytes, media_type: str) -> None:
    """用真实文件结构确认 manifest 声明的媒体类型。

    输入参数：
        payload：已通过大小与 SHA-256 门禁的完整字节。
        media_type：strict gold manifest 的固定 MIME。
    输出返回值：
        无；JPEG SOI/EOI，或 DOCX/PPTX/XLSX 的 ZIP CRC、必要主成员
        与 ``[Content_Types].xml`` main type 一致时返回。
    异常：
        PipelineImplicitGoldIntegrityError：magic、ZIP CRC、成员唯一性、
        必需主成员或 OOXML main content type 不匹配。
    """

    if media_type == _JPEG:
        if not (payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")):
            raise PipelineImplicitGoldIntegrityError()
        return
    ooxml_types = {
        _DOCX: (
            "word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        ),
        _PPTX: (
            "ppt/presentation.xml",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        ),
        _XLSX: (
            "xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        ),
    }
    ooxml_identity = ooxml_types.get(media_type)
    if ooxml_identity is None:
        raise PipelineImplicitGoldIntegrityError()
    required_member, main_content_type = ooxml_identity
    _verify_ooxml_main_type(
        payload,
        required_member=required_member,
        main_content_type=main_content_type,
    )


def _verify_ooxml_main_type(
    payload: bytes,
    *,
    required_member: str,
    main_content_type: str,
) -> None:
    """核验一份已固定字节的 OOXML ZIP CRC 与主类型。

    输入参数：
        payload：已通过 strict manifest size/SHA-256 的完整字节。
        required_member：DOCX/PPTX/XLSX 对应的 OOXML 主文档成员。
        main_content_type：与主成员对应的非宏 OOXML MIME。
    输出返回值：
        无；central directory 无便携碰撞、CRC 完整、必要成员
        存在且 content-types 仅声明一个匹配 main type 时返回。
    异常：
        PipelineImplicitGoldIntegrityError：容器、路径、CRC、XML 或主类型
            任一不可信。
    """

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            portable_names = {
                unicodedata.normalize("NFC", name).casefold() for name in names
            }
            if (
                len(names) != len(set(names))
                or len(names) != len(portable_names)
                or "[Content_Types].xml" not in names
                or required_member not in names
                or archive.testzip() is not None
            ):
                raise PipelineImplicitGoldIntegrityError()
            content_type_info = archive.getinfo("[Content_Types].xml")
            if not 0 < content_type_info.file_size <= 1_048_576:
                raise PipelineImplicitGoldIntegrityError()
            content_types = archive.read(content_type_info)
            lowered = content_types.replace(b"\x00", b"").lower()
            if b"<!doctype" in lowered or b"<!entity" in lowered:
                raise PipelineImplicitGoldIntegrityError()
            root = ET.fromstring(content_types)
            if root.tag != _CONTENT_TYPES_ROOT or any(
                item.tag not in {_CONTENT_TYPES_DEFAULT, _CONTENT_TYPES_OVERRIDE}
                for item in root
            ):
                raise PipelineImplicitGoldIntegrityError()
            matches = [
                item
                for item in root
                if item.tag == _CONTENT_TYPES_OVERRIDE
                and item.attrib.get("PartName") == f"/{required_member}"
                and item.attrib.get("ContentType") == main_content_type
            ]
            if len(matches) != 1:
                raise PipelineImplicitGoldIntegrityError()
    except PipelineImplicitGoldIntegrityError:
        raise
    except (
        ET.ParseError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        raise PipelineImplicitGoldIntegrityError() from None


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """构造 JSON object 并拒绝任何重复字段名。

    输入参数：
        pairs：标准库 JSON decoder 保留原始顺序的字段对列表。
    输出返回值：
        字段名唯一时返回普通字典。
    异常：
        PipelineImplicitGoldManifestError：同一 object 出现重复字段。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PipelineImplicitGoldManifestError()
        result[key] = value
    return result


def _reject_non_standard_constant(value: str) -> None:
    """拒绝 JSON 标准外的 NaN 与正负 Infinity token。

    输入参数：
        value：decoder 遇到的非标准常量文本。
    输出返回值：
        不返回；恒抛固定 manifest 错误。
    """

    del value
    raise PipelineImplicitGoldManifestError()


def _expected_files(
    task_id: str,
    role: str,
) -> tuple[tuple[str, int, str, str], ...]:
    """返回一个角色的固定 path→真实字节身份闭集。

    输入参数：
        task_id：已注册的 CombinationDocs-002、Excel-008、PPT-003
            或 SearchWrite-008。
        role：``input``、``gold`` 或 Combo 专属 ``known_negative``。
    输出返回值：
        按 UTF-8 路径字节序排序的任务专属不可变元组；
        PPT-003 gold 另包含 source-copy 和 12 个内容复用分类副本。
    异常：
        PipelineImplicitGoldManifestError：角色不在闭集。
    """

    if role not in {"input", "gold", "known_negative"}:
        raise PipelineImplicitGoldManifestError()
    if task_id == COMBINATION002_TASK_ID:
        if role == "input":
            return _COMBINATION002_INPUT_FILES
        if role == "known_negative":
            return _COMBINATION002_KNOWN_NEGATIVE_FILES
        raise PipelineImplicitGoldManifestError()
    if role == "known_negative":
        raise PipelineImplicitGoldManifestError()
    if task_id == EXCEL008_TASK_ID:
        return _EXCEL008_INPUT_FILES if role == "input" else _EXCEL008_GOLD_FILES
    if task_id == SEARCHWRITE008_TASK_ID:
        return _SEARCHWRITE_INPUT_FILES if role == "input" else _SEARCHWRITE_GOLD_FILES
    if task_id != PPT003_TASK_ID:
        raise PipelineImplicitGoldManifestError()
    input_files = tuple(
        sorted(
            (*_SOURCE_IMAGES, *_PRESENTATIONS),
            key=lambda item: item[0].encode("utf-8"),
        )
    )
    if role == "input":
        return input_files
    by_path = {item[0]: item for item in _SOURCE_IMAGES}
    copied_files = tuple(
        (destination, *by_path[source][1:])
        for destination, source in _CLASSIFIED_COPIES
    )
    return tuple(
        sorted(
            (*input_files, *copied_files),
            key=lambda item: item[0].encode("utf-8"),
        )
    )


def serialize_pipeline_implicit_asset_manifest(
    document: dict[str, Any],
) -> bytes:
    """把正式 manifest object 编码为唯一 UTF-8 JSON 字节。

    输入参数：
        document：builder 返回的一份 JSON object。
    输出返回值：
        两空格缩进、保留 Unicode、末尾单换行的确定性字节。
    """

    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_ppt003_asset_manifest_files(repo_root: Path) -> None:
    """确定性写入 PPT-003 的两份正式资产清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；仅覆盖 builder 固定职责范围内的 input/gold manifest。
    """

    _write_asset_manifest_files(repo_root, task_id=PPT003_TASK_ID)


def write_combination002_asset_manifest_files(repo_root: Path) -> None:
    """确定性写入 CombinationDocs-002 的两份正式清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；只覆盖该任务的 input/gold manifest，不修改任何派生清单。
    """

    _write_asset_manifest_files(repo_root, task_id=COMBINATION002_TASK_ID)


def write_excel008_asset_manifest_files(repo_root: Path) -> None:
    """确定性写入 Excel-008 的 input/gold 正式清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；仅写入 Excel-008 的两份 task-specific manifest，
        不修改 canonical、release、runtime-support 或 site。
    """

    _write_asset_manifest_files(repo_root, task_id=EXCEL008_TASK_ID)


def write_searchwrite008_asset_manifest_files(repo_root: Path) -> None:
    """确定性写入 SearchWrite-008 的 input/gold 正式清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；仅写入 SearchWrite 明确注册的两个 task-specific manifest，
        不修改 canonical、release、runtime-support、site 或 generated 文件。
    """

    _write_asset_manifest_files(repo_root, task_id=SEARCHWRITE008_TASK_ID)


def _write_asset_manifest_files(repo_root: Path, *, task_id: str) -> None:
    """确定性写入一个已注册任务的 input/gold 清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
        task_id：精确四任务注册表中的 canonical ID。
    输出返回值：
        无；仅覆盖 builder 为该任务返回的两个正式 manifest 路径。
    """

    for relative_path, document in build_pipeline_implicit_asset_manifest_documents(
        repo_root,
        task_id=task_id,
    ).items():
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(serialize_pipeline_implicit_asset_manifest(document))


def check_ppt003_asset_manifest_files(repo_root: Path) -> bool:
    """逐字节检查 PPT-003 正式 input/gold 清单是否漂移。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        两份文件均存在且等于确定性 builder 输出时返回 ``True``；
        读取失败、canonical 身份漂移或任一字节不同均返回 ``False``。
    """

    return _check_asset_manifest_files(repo_root, task_id=PPT003_TASK_ID)


def check_combination002_asset_manifest_files(repo_root: Path) -> bool:
    """逐字节检查 CombinationDocs-002 正式清单是否漂移。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        两份文件都等于固定 builder 输出时为 ``True``，否则为 ``False``。
    """

    return _check_asset_manifest_files(
        repo_root,
        task_id=COMBINATION002_TASK_ID,
    )


def check_excel008_asset_manifest_files(repo_root: Path) -> bool:
    """逐字节检查 Excel-008 正式 input/gold 清单是否漂移。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        两份文件均存在且与固定 builder 输出一致时返回
        ``True``；缺失、读取失败或任一字节不同返回 ``False``。
    """

    return _check_asset_manifest_files(repo_root, task_id=EXCEL008_TASK_ID)


def check_searchwrite008_asset_manifest_files(repo_root: Path) -> bool:
    """逐字节检查 SearchWrite-008 正式 input/gold 清单是否漂移。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        两份文件均存在且与固定 builder 输出相同时返回 ``True``；
        缺失、读取失败、canonical 身份漂移或任一字节差异返回 ``False``。
    """

    return _check_asset_manifest_files(
        repo_root,
        task_id=SEARCHWRITE008_TASK_ID,
    )


def _check_asset_manifest_files(repo_root: Path, *, task_id: str) -> bool:
    """检查一个已注册任务的两份正式 manifest 是否确定性一致。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
        task_id：精确四任务注册表中的 canonical ID。
    输出返回值：
        builder 身份和全部落盘字节一致时为 ``True``，否则为 ``False``。
    """

    try:
        documents = build_pipeline_implicit_asset_manifest_documents(
            repo_root,
            task_id=task_id,
        )
    except PipelineImplicitGoldManifestError:
        return False
    for relative_path, document in documents.items():
        try:
            actual = (repo_root / relative_path).read_bytes()
        except OSError:
            return False
        if actual != serialize_pipeline_implicit_asset_manifest(document):
            return False
    return True


def _load_canonical_identity(
    repo_root: Path,
    *,
    task_id: str,
) -> dict[str, Any]:
    """读取并确认 pipeline canonical 文件名与内部任务身份一致。

    输入参数：
        repo_root：包含 ``benchmark/tasks`` 的仓库根。
        task_id：已通过注册表确认的 canonical 任务 ID。
    输出返回值：
        已确认为 object 且 task_id 正确的 canonical task。
    异常：
        PipelineImplicitGoldManifestError：路径、JSON 或任务身份无效。
    """

    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise PipelineImplicitGoldManifestError("pipeline repo root 无效")
    try:
        task = json.loads(
            (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PipelineImplicitGoldManifestError(
            "pipeline canonical task 无法读取"
        ) from None
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise PipelineImplicitGoldManifestError("pipeline canonical task 身份漂移")
    return task


def _task_identity(task_id: str) -> tuple[str, str, str]:
    """返回注册任务的 UID 及 input/gold manifest 位置。

    输入参数：
        task_id：候选 pipeline-implicit canonical ID。
    输出返回值：
        ``(task_uid, input_manifest_path, gold_manifest_path)``。
    异常：
        PipelineImplicitGoldManifestError：任务不在精确四任务闭集中。
    """

    identities = {
        COMBINATION002_TASK_ID: (
            COMBINATION002_TASK_UID,
            COMBINATION002_INPUT_MANIFEST_PATH,
            COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
        ),
        EXCEL008_TASK_ID: (
            EXCEL008_TASK_UID,
            EXCEL008_INPUT_MANIFEST_PATH,
            EXCEL008_GOLD_MANIFEST_PATH,
        ),
        PPT003_TASK_ID: (
            PPT003_TASK_UID,
            PPT003_INPUT_MANIFEST_PATH,
            PPT003_GOLD_MANIFEST_PATH,
        ),
        SEARCHWRITE008_TASK_ID: (
            SEARCHWRITE008_TASK_UID,
            SEARCHWRITE008_INPUT_MANIFEST_PATH,
            SEARCHWRITE008_GOLD_MANIFEST_PATH,
        ),
    }
    try:
        return identities[task_id]
    except (KeyError, TypeError):
        raise PipelineImplicitGoldManifestError() from None


def _build_input_manifest(
    task_id: str,
    task_uid: str,
    files: tuple[tuple[str, int, str, str], ...],
) -> dict[str, Any]:
    """构造可被通用 input 资产安全链直接消费的 manifest。

    输入参数：
        task_id/task_uid：已注册 canonical 任务身份。
        files：按 UTF-8 字节序排列的 path/size/SHA/MIME 元组。
    输出返回值：
        与 ``runtime.assets.AssetManifest`` 完全兼容的 download-only object；
        后续 canonical 可复用既有 fetch/verify/upload，不另建下载器。
    """

    return {
        "schema_version": 1,
        "asset_set_id": task_id,
        "source": {
            "provider": "huggingface_dataset",
            "repository": _LEE_REPOSITORY,
            "revision": _LEE_REVISION,
            "base_path": f"benchmark_dataset/{task_uid}",
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": path,
                "size": size_bytes,
                "sha256": sha256,
                "media_type": media_type,
            }
            for path, size_bytes, sha256, media_type in files
        ],
    }


def _build_gold_manifest(
    task_id: str,
    task_uid: str,
    files: tuple[tuple[str, int, str, str], ...],
) -> dict[str, Any]:
    """构造 pipeline 任务专属的严格 gold manifest object。

    输入参数：
        task_id/task_uid：已注册 canonical 任务身份。
        files：按 UTF-8 字节序排列的 path/size/SHA/MIME 元组。
    输出返回值：
        保留未核验许可、仅允许下载分发且不套用 OSWorld UID 的
        pipeline-implicit gold object。
    """

    return {
        "schema_version": 1,
        "manifest_id": f"{task_id}-gold-v1",
        "task_id": task_id,
        "task_uid": task_uid,
        "manifest_role": "gold",
        "source": {
            "provider": "huggingface_dataset",
            "repository": _LEE_REPOSITORY,
            "revision": _LEE_REVISION,
            "base_path": f"answer_files/{task_uid}",
        },
        "license": {
            "status": "unverified",
            "spdx_expression": None,
            "evidence_ref": _LICENSE_EVIDENCE_REF,
            "distribution": "download_only",
        },
        "distribution_policy": "download_only",
        "entries": [
            {
                "path": path,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "media_type": media_type,
            }
            for path, size_bytes, sha256, media_type in files
        ],
    }


def _build_combination002_known_negative_manifest(
    files: tuple[tuple[str, int, str, str], ...],
) -> dict[str, Any]:
    """构造 CombinationDocs-002 历史 HF answer 的 audit-only 合同。

    输入参数：files 为固定 revision 的三份错误 answer 字节身份。
    输出返回值：显式禁止作为 pass oracle，并锁定正式 evaluator
        ``FAIL 2/3`` 结果的 known-negative manifest object。
    """

    return {
        "schema_version": 1,
        "manifest_id": f"{COMBINATION002_TASK_ID}-hf-answer-known-negative-v1",
        "task_id": COMBINATION002_TASK_ID,
        "task_uid": COMBINATION002_TASK_UID,
        "manifest_role": "audit_known_negative",
        "use_as_pass_oracle": False,
        "source": {
            "provider": "huggingface_dataset",
            "repository": _LEE_REPOSITORY,
            "revision": _LEE_REVISION,
            "base_path": f"answer_files/{COMBINATION002_TASK_UID}",
        },
        "license": {
            "status": "unverified",
            "spdx_expression": None,
            "evidence_ref": _LICENSE_EVIDENCE_REF,
            "distribution": "download_only",
        },
        "distribution_policy": "download_only",
        "expected_evaluation": {
            "protocol_id": "paraguibench.operation.cross-document-facts.v1",
            "passed": False,
            "score": 0.6667,
            "required_fact_count": 3,
            "matched_fact_count": 2,
            "reason_codes": ["DOCX_PROFIT_ORDER_INCORRECT"],
        },
        "entries": [
            {
                "path": path,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "media_type": media_type,
            }
            for path, size_bytes, sha256, media_type in files
        ],
    }


__all__ = [
    "COMBINATION002_INPUT_MANIFEST_PATH",
    "COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH",
    "COMBINATION002_TASK_ID",
    "COMBINATION002_TASK_UID",
    "EXCEL008_GOLD_MANIFEST_PATH",
    "EXCEL008_INPUT_MANIFEST_PATH",
    "EXCEL008_TASK_ID",
    "EXCEL008_TASK_UID",
    "PPT003_GOLD_MANIFEST_PATH",
    "PPT003_INPUT_MANIFEST_PATH",
    "PPT003_TASK_ID",
    "PPT003_TASK_UID",
    "SEARCHWRITE008_GOLD_MANIFEST_PATH",
    "SEARCHWRITE008_INPUT_MANIFEST_PATH",
    "SEARCHWRITE008_TASK_ID",
    "SEARCHWRITE008_TASK_UID",
    "PipelineImplicitGoldEntry",
    "PipelineImplicitGoldIntegrityError",
    "PipelineImplicitGoldManifest",
    "PipelineImplicitGoldManifestError",
    "PipelineImplicitKnownNegativeIntegrityError",
    "PipelineImplicitKnownNegativeManifest",
    "PipelineImplicitKnownNegativeManifestError",
    "VerifiedPipelineImplicitGoldBundle",
    "VerifiedPipelineImplicitGoldFile",
    "VerifiedPipelineImplicitKnownNegativeBundle",
    "VerifiedPipelineImplicitKnownNegativeFile",
    "build_excel008_asset_manifest_documents",
    "build_combination002_asset_manifest_documents",
    "build_ppt003_asset_manifest_documents",
    "build_pipeline_implicit_asset_manifest_documents",
    "build_searchwrite008_asset_manifest_documents",
    "check_excel008_asset_manifest_files",
    "check_combination002_asset_manifest_files",
    "check_ppt003_asset_manifest_files",
    "check_searchwrite008_asset_manifest_files",
    "load_verified_pipeline_implicit_gold_manifest",
    "load_pipeline_implicit_known_negative_manifest",
    "resolve_pipeline_implicit_known_negative_bundle",
    "resolve_verified_pipeline_implicit_gold_bundle",
    "serialize_pipeline_implicit_asset_manifest",
    "write_ppt003_asset_manifest_files",
    "write_combination002_asset_manifest_files",
    "write_excel008_asset_manifest_files",
    "write_searchwrite008_asset_manifest_files",
]
