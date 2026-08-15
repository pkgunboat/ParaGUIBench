#!/usr/bin/env python3
"""确定性生成 13 个 legacy OSWorld state 任务的资产与 gold 草案。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


XLANG_REPOSITORY = "xlangai/ubuntu_osworld_file_cache"
XLANG_REVISION = "711e0811642364e7aa8f10a8918367d0b626d578"
_INPUT_DRAFT_ROOT = PurePosixPath("benchmark/assets/manifests/osworld-state-drafts")
_GOLD_DRAFT_ROOT = PurePosixPath("benchmark/gold/manifests/osworld-state-drafts")
_SOURCE_CONFIG_INPUT_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-CombinationDocs-009",
        "Operation-FileOperate-CombinationDocs-012",
        "Operation-FileOperate-SearchAndWrite-003",
        "Operation-FileOperate-SearchAndWrite-005",
    }
)

_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP = "application/zip"
_PNG = "image/png"
_CSV = "text/csv"
_TEXT = "text/plain"
_MP4 = "video/mp4"

# Input tuple fields: remote basename, guest-home-relative path, purpose,
# expected media type. 远端 basename 来自 canonical direct URL 或最终
# OSWorld source task config；builder 再按固定 repository/revision 加前缀。
_FUNDING_PDFS = tuple(
    [
        (
            f"ecs{year}.pdf",
            f"Documents/Fundings/ecs/ecs{year}.pdf",
            "reference_input",
            _PDF,
        )
        for year in range(15, 24)
    ]
    + [
        (
            "customer-information-sheet-for-inward-payments-to-hong-kong.pdf",
            (
                "Documents/Fundings/grf/"
                "customer-information-sheet-for-inward-payments-to-hong-kong.pdf"
            ),
            "reference_input",
            _PDF,
        )
    ]
    + [
        (
            f"grf{year}.pdf",
            f"Documents/Fundings/grf/grf{year}.pdf",
            "reference_input",
            _PDF,
        )
        for year in range(15, 24)
    ]
)

_TASK_INPUTS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "Operation-FileOperate-BatchOperation-003": (
        ("raw_book.zip", "Desktop/book.zip", "task_input_bundle", _ZIP),
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        (
            "lecture1-2021-with-ink.pptx",
            "Desktop/lecture1-2021-with-ink.pptx",
            "editable_target",
            _PPTX,
        ),
        ("notes.docx", "Desktop/notes.docx", "reference_input", _DOCX),
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        ("exam.zip", "exam.zip", "task_input_bundle", _ZIP),
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        (
            "invoice TII-20220301-90.pdf",
            "Desktop/invoice TII-20220301-90.pdf",
            "reference_input",
            _PDF,
        ),
        (
            "Invoice # GES-20220215-82.pdf",
            "Desktop/Invoice # GES-20220215-82.pdf",
            "reference_input",
            _PDF,
        ),
        (
            "Invoice # 243729.pdf",
            "Desktop/Invoice # 243729.pdf",
            "reference_input",
            _PDF,
        ),
        (
            "Bank-Statement.pdf",
            "Desktop/Bank-Statement.pdf",
            "reference_input",
            _PDF,
        ),
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        (
            "Zheng He .docx",
            "Desktop/students work/Zheng He .docx",
            "context_input",
            _DOCX,
        ),
        (
            "The literature reviews of weekly readings.docx",
            "Desktop/students work/The literature reviews of weekly readings.docx",
            "context_input",
            _DOCX,
        ),
        (
            "The British Justice System.docx",
            "Desktop/students work/The British Justice System.docx",
            "context_input",
            _DOCX,
        ),
        (
            "quiz2.docx",
            "Desktop/students work/quiz2.docx",
            "context_input",
            _DOCX,
        ),
        (
            "quiz.docx",
            "Desktop/students work/quiz.docx",
            "context_input",
            _DOCX,
        ),
        (
            "Q1&2&3.docx",
            "Desktop/students work/Q1&2&3.docx",
            "context_input",
            _DOCX,
        ),
        (
            "Photo Ethics in Journalism.docx",
            "Desktop/students work/Photo Ethics in Journalism.docx",
            "context_input",
            _DOCX,
        ),
        (
            "cassie.docx",
            "Desktop/students work/cassie.docx",
            "context_input",
            _DOCX,
        ),
        (
            "case study.docx",
            "Desktop/students work/case study.docx",
            "editable_target",
            _DOCX,
        ),
        (
            "irregularrules02.pdf",
            "Desktop/Grammar rules PDF/irregularrules02.pdf",
            "context_input",
            _PDF,
        ),
        (
            "irregularrules01.pdf",
            "Desktop/Grammar rules PDF/irregularrules01.pdf",
            "context_input",
            _PDF,
        ),
        (
            "fragrules.pdf",
            "Desktop/Grammar rules PDF/fragrules.pdf",
            "context_input",
            _PDF,
        ),
        (
            "csfsrules.pdf",
            "Desktop/Grammar rules PDF/csfsrules.pdf",
            "context_input",
            _PDF,
        ),
        (
            "Public Lecture Teaching Plan.docx",
            "Desktop/Public Lecture Teaching Plan.docx",
            "context_input",
            _DOCX,
        ),
        (
            "Course Timetable.xlsx",
            "Desktop/Course Timetable.xlsx",
            "context_input",
            _XLSX,
        ),
    ),
    "Operation-FileOperate-CombinationDocs-013": _FUNDING_PDFS,
    "Operation-FileOperate-CombinationDocs-014": (
        *_FUNDING_PDFS,
        (
            "supported_rate.xlsx",
            "Documents/Fundings/supported_rate.xlsx",
            "editable_target",
            _XLSX,
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        (
            "Professor_Contact.xlsx",
            "Desktop/Professor_Contact.xlsx",
            "editable_target",
            _XLSX,
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        (
            "2023_validation_Book_Reading_Rate.xlsx",
            "Desktop/2023_validation_Book_Reading_Rate.xlsx",
            "reference_input",
            _XLSX,
        ),
        (
            "book_list_result.docx",
            "Desktop/book_list_result.docx",
            "editable_target",
            _DOCX,
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        (
            "best_awards_acl.xlsx",
            "Desktop/best_awards_acl.xlsx",
            "editable_target",
            _XLSX,
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        ("movies.xlsx", "Desktop/movies.xlsx", "editable_target", _XLSX),
    ),
    "Operation-FileOperate-Settings-001": (
        (
            "Robotic_Workshop_Infographics.pptx",
            "Desktop/Robotic_Workshop_Infographics.pptx",
            "editable_target",
            _PPTX,
        ),
        ("landscape.mp4", "Desktop/landscape.mp4", "reference_input", _MP4),
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        (
            "MUST_VISIT.xlsx",
            "Desktop/MUST_VISIT.xlsx",
            "editable_target",
            _XLSX,
        ),
        (
            "restaurants.txt",
            "Desktop/restaurants.txt",
            "reference_input",
            _TEXT,
        ),
    ),
}

# Gold tuple fields: remote basename and expected media type. 除明确冻结的历史
# 负样本外，logical key 和 expected index 只来自 ArtifactEvidenceSpec catalog。
_TASK_GOLD: dict[str, tuple[tuple[str, str], ...]] = {
    "Operation-FileOperate-BatchOperation-003": (("book.zip", _ZIP),),
    "Operation-FileOperate-CombinationDocs-009": (
        ("lecture1-2021-with-ink_Gold.pptx", _PPTX),
    ),
    "Operation-FileOperate-CombinationDocs-010": (("grades.xlsx", _XLSX),),
    "Operation-FileOperate-CombinationDocs-011": (("Invoice # 243729.pdf", _PDF),),
    "Operation-FileOperate-CombinationDocs-012": (("case study gold.docx", _DOCX),),
    "Operation-FileOperate-CombinationDocs-013": (
        ("GRF-p5y.bak.xlsx", _XLSX),
        ("GRF-p5y.bak-Sheet1.csv", _CSV),
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        ("supported_rate_gt.xlsx", _XLSX),
        ("supported_rate_gt.csv", _CSV),
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        ("Professor_Contact_Gold.xlsx", _XLSX),
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        ("book_list_result_Gold.docx", _DOCX),
    ),
    "Operation-FileOperate-SearchAndWrite-005": (("gold_best_awards_acl.xlsx", _XLSX),),
    "Operation-FileOperate-SearchAndWrite-009": (("gold_movies.xlsx", _XLSX),),
    "Operation-FileOperate-Settings-001": (("landscape.png", _PNG),),
    "Operation-WebOperate-SearchAndWrite-001": (("MUST_VISIT_gold.xlsx", _XLSX),),
}

# Settings 的远端 landscape.png 指向已证伪的 9.042 秒旧图；它只能作为
# v1/unverified 历史草案保留。正式 evaluator 已切到私有派生 v2 清单，
# draft 生成器不得把旧远端 locator 冒充为 v2 gold identity。
_HISTORICAL_GOLD_DRAFT_KEYS: dict[str, tuple[str, ...]] = {
    "Operation-FileOperate-Settings-001": (
        "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v1",
    ),
}

# 逐条只登记已经从固定 revision 匿名读取并完成 size/SHA 核验的字节。
# key 使用 canonical task、角色与完整远端相对路径，避免 basename 碰撞。
_VERIFIED_INTEGRITY: dict[str, tuple[int, str]] = {
    "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/raw_book.zip": (
        1_091_801,
        "f4c410119a88653225d8016d2594ae395d5b020e7b40067af0e72f0754b3c22e",
    ),
    "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/book.zip": (
        2_935_633,
        "5d028f5cb57e8f04fd8e5a65370959da91e7c873601bc1fcff9dc8ff5b72005f",
    ),
    "multi_apps/eb303e01-261e-4972-8c07-c9b4e7a4922a/lecture1-2021-with-ink.pptx": (
        118_598,
        "ab4b65d92b40ad172f01f2ca5d7decd72fb1e970dc6ff2f7e8d25b09c7b57b44",
    ),
    "multi_apps/eb303e01-261e-4972-8c07-c9b4e7a4922a/notes.docx": (
        7_377,
        "2374d7b73e04d5d0fa3f3b8a5001da0ce17ee0318ec49d2280d6aad68f2efc32",
    ),
    "multi_apps/eb303e01-261e-4972-8c07-c9b4e7a4922a/lecture1-2021-with-ink_Gold.pptx": (
        119_034,
        "dbefcaa98ecf1511016ac3063cba1c2ca70ac7e51d5e590d773afa95cb7591e0",
    ),
    "multi_apps/aceb0368-56b8-4073-b70e-3dc9aee184e0/exam.zip": (
        387_112,
        "10d6ef9c161b2bbb6eb6515f0e0c1717c39f675d7b569fcac55477f176b1c7c1",
    ),
    "multi_apps/aceb0368-56b8-4073-b70e-3dc9aee184e0/grades.xlsx": (
        9_614,
        "7e6b3a6dae808cef87b2847933db04eb2138d82cf1d7b354ff1bbc88bb86f842",
    ),
    "multi_apps/337d318b-aa07-4f4f-b763-89d9a2dd013f/Bank-Statement.pdf": (
        122_969,
        "4c2fcda1e52bfd4e6d81d241f8a380aafcb5f6e735a7a9d77ea7e8fb62d7f4e7",
    ),
    "multi_apps/337d318b-aa07-4f4f-b763-89d9a2dd013f/Invoice # 243729.pdf": (
        15_655,
        "39c23a5792795583184c3694de5b0187d895e6ef75d2e2cbfbdd16b23f1c3594",
    ),
    "multi_apps/337d318b-aa07-4f4f-b763-89d9a2dd013f/Invoice # GES-20220215-82.pdf": (
        42_896,
        "729939cc631fccecaed8a5583a9beee0971d8d01724afcca899627f7c9b98c6f",
    ),
    "multi_apps/337d318b-aa07-4f4f-b763-89d9a2dd013f/invoice TII-20220301-90.pdf": (
        64_410,
        "6d116abe3eaac051dfe4f484ab5d3a5b1298601dc6026ffc1c683172463ebcb1",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/Course Timetable.xlsx": (
        10_118,
        "be24552315b6709a1eb8532439f05c6ff5e4376653fef1ba99bda231e9a8c9c3",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/Photo Ethics in Journalism.docx": (
        76_468,
        "9a1f810d97356fc2d8181889e6f54986ed669ca5d6091ef065f189f5061b529d",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/Public Lecture Teaching Plan.docx": (
        13_987,
        "68b07805dbb9859a705948903cc8efd6c6af28d86b35735c57385524bbbe2a3e",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/Q1&2&3.docx": (
        170_816,
        "43d2efb9f0b3f9fc460f6fd0eff96c36e658909fc734ea686d98af4fed7fa269",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/The British Justice System.docx": (
        15_153,
        "97161ddf0050e35fb7205a1d2878fae6e1cb776f94b0aec2cc88efcd4b18da79",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/The literature reviews of weekly readings.docx": (
        17_954,
        "6c1dad342cf9269260a1a1ea00a5debe3851123a851e4e2c27fddd27ca8f973f",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/Zheng He .docx": (
        22_443,
        "1cd5478b23ca18bf9f4711213fdfc2ab6f07412b5646a284247f38aa7d7a7ebc",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/case study gold.docx": (
        16_501,
        "8dee945aea1932a50da5600e6eedd37316b5cc70ef944a991e9f0666d763cd57",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/case study.docx": (
        16_470,
        "3f5c324a1cc3a42e6b39fe31434f1c14f3afcffe6b57f9d34baec193006e897b",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/cassie.docx": (
        285_893,
        "ba92e595b3a2d2147e7fa2bda12d5eb439d4d703ec37262d7ddc15a8e6d65799",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/csfsrules.pdf": (
        262_735,
        "2ba19089498561dcdfb1255e0c5bd81aa1704dc4021cf518c094150d27fd3dfa",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/fragrules.pdf": (
        351_429,
        "ca7dfa501f1deb9b08a3bf7e80e5dffb8c5f393c5e883c84b8a8cf40b9ad40b4",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/irregularrules01.pdf": (
        418_815,
        "cf622918f45eb399a2159ab07c2f93a2919b49a9ab553549cf6c3f3d831b96ef",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/irregularrules02.pdf": (
        247_493,
        "8bc8de47fda4dbf497eb63c14565fa6296dc5dcfe18dae5f052ee189738ca6e1",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/quiz.docx": (
        466_557,
        "78054d1275f32edaa19f45b49fca6d03f53d2962df9aa809771874951a506207",
    ),
    "multi_apps/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e/quiz2.docx": (
        14_888,
        "58ea885c3d8e768e17a30ad7360f4d851417831c1f45bb1ee489260a412c29ed",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/customer-information-sheet-for-inward-payments-to-hong-kong.pdf": (
        325_824,
        "6917e48e10e0e97d681f821f7c3ec3556ff2917901bf14f5d49727c5bbc3a8b3",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs15.pdf": (
        142_850,
        "83f5aa554bb129b431733ac9157fb0024b3d17a0ccb193e557fd6b9deead3a7d",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs16.pdf": (
        83_414,
        "b495e549d5920a7e01fd09ea1d73630442ba57a4686ffb2e5507658ad22a3cd4",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs17.pdf": (
        61_563,
        "c069c5beae098968b894d2995230ef318efcf55646fe66b14fae69c347afa435",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs18.pdf": (
        34_295,
        "ed32fe82f027b74f7f15ddba998c5fb9e596495823a071cecc8fa66395b54377",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs19.pdf": (
        52_415,
        "ee4a6059222c436a10fad59859f26086466f4c35fcf0e8ae8f1114b858603936",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs20.pdf": (
        71_781,
        "1235e7542c5d5f11e07f5091f9103d84094c56938880604d813d557a7358b013",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs21.pdf": (
        60_133,
        "c31e5e282176184d3c89d89bd5ee301a668dd8d04eb993447b3fce06ed464ffd",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs22.pdf": (
        34_505,
        "23f870f0d7844bf4e33d5e3e75be699d16d46d147273ee24cc5483fb41362b3b",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/ecs23.pdf": (
        39_866,
        "813ccb13a3293f4506998b0c0041099b8d6827ec9090942adcdb61292dd2da07",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf15.pdf": (
        104_127,
        "8b261f7626f6a8ba9cc18d829353e5b179d377e7249dd72ac514ea068ce7bfae",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf16.pdf": (
        65_416,
        "8bae643fe33fba98aa467619d9278caa5d2693ffa5a19dda758714a700265253",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf17.pdf": (
        62_448,
        "fe9c5a6272791118a94386ecd70e0198a5b1daff7757816dbafafaf1e0a88d03",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf18.pdf": (
        36_777,
        "75aabf4ac6be1810f56e49c4b8ffe2e54cd242cab02c8cdcb7e178d63f79965f",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf19.pdf": (
        53_233,
        "f4259b1dd297d7402fa5b59c23216c91b5a3b93dd1fdf4eb16aedefb639fc904",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf20.pdf": (
        59_534,
        "992efbaf69d2a8215dbfd79d35a6351e81ffba93f498c6db2213c14ac568048d",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf21.pdf": (
        61_448,
        "c5f4561313fa63a1510a0b9f8aff3945626db5014022f7a4706ab29a721e4292",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf22.pdf": (
        35_734,
        "9dd404f1617523ba087fbc9fba6dbbbb1ed093b992072f22d197966e6ec4dab1",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/grf23.pdf": (
        40_536,
        "b41ae005ed3a09f73dc2a1cfb406a1e6b14423293e49f8c00729cc89cdf5bba5",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/supported_rate.xlsx": (
        6_122,
        "f827b1b621e44d1be49e742766b0f20659bad1ae067ab6ee8c2d2f510b22576e",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/supported_rate_gt.xlsx": (
        6_562,
        "9d97b986f83c1871831933499ff69de7198d99b3be0b9347ef8ab1559073fca0",
    ),
    "multi_apps/881deb30-9549-4583-a841-8270c65f2a17/supported_rate_gt.csv": (
        593,
        "c238cedb3a42969b98b7e17a0901925d93a60631d30d8786b6e53a1cef1c09bc",
    ),
    "multi_apps/c7c1e4c3-9e92-4eba-a4b8-689953975ea4/Professor_Contact.xlsx": (
        5_910,
        "85e526d49bf00541382fd0e6469e7cd4ccce7f8dca7b947d412f22cf478eeccc",
    ),
    "multi_apps/c7c1e4c3-9e92-4eba-a4b8-689953975ea4/Professor_Contact_Gold.xlsx": (
        5_794,
        "2e30e2af19a57982b7f0e5c077ccc256b39e49d9c923bc53c4fdbb1ea0b31dc5",
    ),
    "multi_apps/da52d699-e8d2-4dc5-9191-a2199e0b6a9b/2023_validation_Book_Reading_Rate.xlsx": (
        9_080,
        "8882c25c4195105a0735b83b1294e200f75bd3cf7e75b358ba6fe8e2ce5ba029",
    ),
    "multi_apps/da52d699-e8d2-4dc5-9191-a2199e0b6a9b/book_list_result.docx": (
        4_679,
        "594044dfbe3a7e468a18d2b6624d41c3391cb38e37b017483b05e997a559092d",
    ),
    "multi_apps/da52d699-e8d2-4dc5-9191-a2199e0b6a9b/book_list_result_Gold.docx": (
        4_339,
        "abb7895fe29369823b8e292d5a7bb8230e6bcc3bf00547dc0a11590fad0dce01",
    ),
    "multi_apps/67890eb6-6ce5-4c00-9e3d-fb4972699b06/best_awards_acl.xlsx": (
        9_234,
        "3d4c12f50a5e42cf1a27d009faad14218796bd20c0004c3908df35a0eb9c1d94",
    ),
    "multi_apps/67890eb6-6ce5-4c00-9e3d-fb4972699b06/gold_best_awards_acl.xlsx": (
        9_723,
        "8550d87120d252d40d9fe336b8e3b7d04da366746fe9f1480144bf894d94e428",
    ),
    "multi_apps/3e3fc409-bff3-4905-bf16-c968eee3f807/movies.xlsx": (
        25_933,
        "0bfb8d1f761b9f0eb33e0afcf9608a20e0639694eeb834ad34e6588e3cca081e",
    ),
    "multi_apps/3e3fc409-bff3-4905-bf16-c968eee3f807/gold_movies.xlsx": (
        28_021,
        "ae9ef79a758de1c5ebbe425eeec6367a3bea83cbce6aab72b1466179c34124f9",
    ),
    "multi_apps/d1acdb87-bb67-4f30-84aa-990e56a09c92/MUST_VISIT.xlsx": (
        11_615,
        "986743e746dcfc3683eea8fffabb820217021a29689d8ae7909b31a0eee75207",
    ),
    "multi_apps/d1acdb87-bb67-4f30-84aa-990e56a09c92/restaurants.txt": (
        77,
        "9c76c729e04c7e7bf1c495013221d764db68f18fc359844d1a91ae7bc1e953d4",
    ),
    "multi_apps/d1acdb87-bb67-4f30-84aa-990e56a09c92/MUST_VISIT_gold.xlsx": (
        12_620,
        "fd60eecfc60cac1be90b81aa83c5a3322f321e39ac1e516bc023d967195bdd54",
    ),
    "multi_apps/47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5/Robotic_Workshop_Infographics.pptx": (
        381_651,
        "48860d92c6cd10e3492abad596fc15c5eb56a1d6d9508c886a75b8757bcad06d",
    ),
    "multi_apps/47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5/landscape.mp4": (
        9_362_831,
        "d39162e1d519e978261ad4ae824d4446f511936c80d5ce2e085cf617eae04c35",
    ),
}

# 匿名 production fetch 已证明 CombinationDocs-013 与 -014 的 19 份
# Fundings PDF 在各自 source-task 目录中逐字节相同。这里仍生成
# source-task-specific key，不把两个 remote locator 折叠成同一条来源。
_VERIFIED_INTEGRITY.update(
    {
        (
            f"multi_apps/7e287123-70ca-47b9-8521-47db09b69b14/{remote_name}"
        ): _VERIFIED_INTEGRITY[
            f"multi_apps/881deb30-9549-4583-a841-8270c65f2a17/{remote_name}"
        ]
        for remote_name, _guest_path, _purpose, _media_type in _FUNDING_PDFS
    }
)
_VERIFIED_INTEGRITY.update(
    {
        "multi_apps/7e287123-70ca-47b9-8521-47db09b69b14/GRF-p5y.bak.xlsx": (
            9_087,
            "23efe99a2167d170b8aefd2e141724270488270b8b237fd7424630b3bbd4c9db",
        ),
        "multi_apps/7e287123-70ca-47b9-8521-47db09b69b14/GRF-p5y.bak-Sheet1.csv": (
            123,
            "fc81865fbdc7b5f8438fb766cdad4e04b92181e7a2dfe995889f17258b975a41",
        ),
    }
)
_PROMOTED_RUNTIME_MANIFESTS: dict[str, tuple[str, str | None]] = {
    "Operation-FileOperate-BatchOperation-003": (
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json",
        "benchmark/gold/manifests/Operation-FileOperate-BatchOperation-003.json",
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-010.json",
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-010.json",
    ),
    **{
        task_id: (
            f"benchmark/assets/manifests/{task_id}.json",
            f"benchmark/gold/manifests/{task_id}.json",
        )
        for task_id in (
            "Operation-FileOperate-CombinationDocs-009",
            "Operation-FileOperate-CombinationDocs-014",
            "Operation-FileOperate-SearchAndWrite-001",
            "Operation-FileOperate-SearchAndWrite-003",
            "Operation-FileOperate-SearchAndWrite-005",
            "Operation-FileOperate-SearchAndWrite-009",
            "Operation-WebOperate-SearchAndWrite-001",
        )
    },
    "Operation-FileOperate-CombinationDocs-011": (
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-011.json",
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-011.json",
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-012.json",
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-012.json",
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-013.json",
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-013.json",
    ),
    "Operation-FileOperate-Settings-001": (
        "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json",
        "benchmark/gold/manifests/Operation-FileOperate-Settings-001.json",
    ),
}


class OSWorldStateAssetDraftError(RuntimeError):
    """表示 state 资产草案目录、身份或落盘副本不闭合。"""


def draft_manifest_relative_path(task_id: str, role: str) -> str:
    """返回逐任务 input/gold 草案的固定仓库相对路径。

    输入参数：
        task_id：13-task 固定闭集中的 canonical ID。
        role：``input`` 或 ``gold``。
    输出返回值：
        使用 POSIX 分隔符的仓库相对 JSON 路径。
    异常：
        OSWorldStateAssetDraftError：任务或角色不在固定闭集。
    """

    if task_id not in _TASK_INPUTS or role not in {"input", "gold"}:
        raise OSWorldStateAssetDraftError("state asset draft identity 无效")
    root = _INPUT_DRAFT_ROOT if role == "input" else _GOLD_DRAFT_ROOT
    return str(root / f"{task_id}.{role}.draft.json")


def build_osworld_state_asset_drafts(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """构造 13×2 份确定性 state input/gold 草案。

    输入参数：
        repo_root：包含 canonical tasks 与当前 artifact evidence spec
            catalog 的 ParaGUIBench 仓库根。
    输出返回值：
        仓库相对路径到 JSON object 的 26 项映射；input 共 71 项、gold
        共 15 项。函数不访问网络、不读取模型凭据，也不写文件。
    异常：
        OSWorldStateAssetDraftError：任务、source identity、gold key 或
            catalog 数量发生漂移。
    """

    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise OSWorldStateAssetDraftError("state asset draft repo root 无效")
    task_ids = set(_TASK_INPUTS)
    if task_ids != set(_TASK_GOLD) or not task_ids.issubset(
        OSWORLD_ARTIFACT_EVIDENCE_SPECS
    ):
        raise OSWorldStateAssetDraftError("state asset draft task 闭集漂移")

    documents: dict[str, dict[str, Any]] = {}
    input_total = 0
    gold_total = 0
    for task_id in sorted(task_ids, key=lambda value: value.encode("utf-8")):
        task = _load_canonical_task(repo_root, task_id)
        evidence_spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
        task_uid = task.get("task_uid")
        if not isinstance(task_uid, str) or not task_uid:
            raise OSWorldStateAssetDraftError("state canonical task UID 无效")
        runtime_gold_keys = tuple(
            key
            for slot in evidence_spec.artifact_slots
            for metric in slot.metrics
            for key in metric.gold_keys
        )
        if len(runtime_gold_keys) != len(_TASK_GOLD[task_id]):
            raise OSWorldStateAssetDraftError("state gold key 闭集漂移")
        gold_keys = _HISTORICAL_GOLD_DRAFT_KEYS.get(task_id, runtime_gold_keys)
        if len(gold_keys) != len(_TASK_GOLD[task_id]):
            raise OSWorldStateAssetDraftError("state historical gold key 闭集漂移")

        input_document = _build_manifest(
            task_id=task_id,
            task_uid=task_uid,
            role="input",
            source_task_id=evidence_spec.source_task_id,
            source_evaluator_id=evidence_spec.source_evaluator_id,
            source_contract_sha256=evidence_spec.source_contract_sha256,
            input_specs=_TASK_INPUTS[task_id],
            gold_specs=(),
            gold_keys=(),
        )
        gold_document = _build_manifest(
            task_id=task_id,
            task_uid=task_uid,
            role="gold",
            source_task_id=evidence_spec.source_task_id,
            source_evaluator_id=evidence_spec.source_evaluator_id,
            source_contract_sha256=evidence_spec.source_contract_sha256,
            input_specs=(),
            gold_specs=_TASK_GOLD[task_id],
            gold_keys=gold_keys,
        )
        documents[draft_manifest_relative_path(task_id, "input")] = input_document
        documents[draft_manifest_relative_path(task_id, "gold")] = gold_document
        input_total += len(input_document["entries"])
        gold_total += len(gold_document["entries"])
    if input_total != 71 or gold_total != 15 or len(documents) != 26:
        raise OSWorldStateAssetDraftError("state asset draft 数量闭包漂移")
    return documents


def serialize_osworld_state_asset_draft(document: dict[str, Any]) -> bytes:
    """把单份 draft document 编码为唯一 UTF-8 JSON 字节。

    输入参数：
        document：``build_osworld_state_asset_drafts`` 返回的 JSON object。
    输出返回值：
        两空格缩进、保留 Unicode、末尾单换行的确定性字节。
    """

    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_osworld_state_asset_drafts(repo_root: Path) -> None:
    """生成并原子职责范围内覆盖 26 份逐任务草案。

    输入参数：
        repo_root：ParaGUIBench 仓库根；父目录按需创建。
    输出返回值：
        无；每个目标只写 builder 的确定性序列化结果。
    """

    for relative_path, document in build_osworld_state_asset_drafts(repo_root).items():
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(serialize_osworld_state_asset_draft(document))


def check_osworld_state_asset_drafts(repo_root: Path) -> bool:
    """检查 26 份落盘草案是否与当前可信目录逐字节一致。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        全部存在且逐字节一致返回 ``True``，否则返回 ``False``；不写文件。
    """

    for relative_path, document in build_osworld_state_asset_drafts(repo_root).items():
        try:
            actual = (repo_root / relative_path).read_bytes()
        except OSError:
            return False
        if actual != serialize_osworld_state_asset_draft(document):
            return False
    return True


def _load_canonical_task(repo_root: Path, task_id: str) -> dict[str, Any]:
    """读取一份固定 canonical task JSON object。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
        task_id：固定 13-task 闭集中的 canonical ID。
    输出返回值：
        已确认 task_id 自洽的 JSON object。
    异常：
        OSWorldStateAssetDraftError：文件不可读、JSON 无效或身份漂移。
    """

    try:
        task = json.loads(
            (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        raise OSWorldStateAssetDraftError("state canonical task 无法读取") from None
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise OSWorldStateAssetDraftError("state canonical task 身份漂移")
    promoted_references = _PROMOTED_RUNTIME_MANIFESTS.get(task_id)
    has_prepare_reference = "prepare_script_path" in task
    if promoted_references is None:
        if (
            "asset_manifest" in task
            or "gold_manifest" in task
            or not has_prepare_reference
            or not isinstance(task.get("prepare_script_path"), str)
            or not task["prepare_script_path"]
        ):
            raise OSWorldStateAssetDraftError("state canonical asset mode 无效")
    else:
        expected_input_reference, expected_gold_reference = promoted_references
        gold_reference_matches = (
            "gold_manifest" not in task
            if expected_gold_reference is None
            else task.get("gold_manifest") == expected_gold_reference
        )
        if (
            has_prepare_reference
            or task.get("asset_manifest") != expected_input_reference
            or not gold_reference_matches
        ):
            raise OSWorldStateAssetDraftError("state canonical asset mode 无效")
    return task


def _build_manifest(
    *,
    task_id: str,
    task_uid: str,
    role: str,
    source_task_id: str,
    source_evaluator_id: str,
    source_contract_sha256: str,
    input_specs: tuple[tuple[str, str, str, str], ...],
    gold_specs: tuple[tuple[str, str], ...],
    gold_keys: tuple[str, ...],
) -> dict[str, Any]:
    """由冻结 task/evidence identity 构造一份角色专属 manifest。

    输入参数：
        task_id/task_uid：canonical 任务身份。
        role：``input`` 或 ``gold``。
        source_task_id/source_evaluator_id/source_contract_sha256：取自当前
            artifact evidence spec 的三重可信来源身份。
        input_specs/gold_specs/gold_keys：该角色的路径与 gold 闭集。
    输出返回值：
        不包含未验证 size/SHA 猜测的 JSON object。
    """

    if role == "input":
        repository, revision, base_path = _input_source(
            task_id,
            task_uid,
            source_task_id,
        )
        path_status = "verified"
        path_evidence = (
            f"osworld-task:{source_task_id}:config"
            if task_id in _PROMOTED_RUNTIME_MANIFESTS
            or task_id in _SOURCE_CONFIG_INPUT_TASK_IDS
            else f"canonical-task:{task_id}:prepare_script_path"
        )
        entries = []
        for remote_name, guest_path, purpose, media_type in sorted(
            input_specs,
            key=lambda item: item[0].encode("utf-8"),
        ):
            remote_path = str(PurePosixPath(base_path) / remote_name)
            entries.append(
                {
                    "remote_relative_path": remote_path,
                    "guest_relative_path": guest_path,
                    "path_status": path_status,
                    "path_evidence_ref": path_evidence,
                    "purpose": purpose,
                    "expected_media_type": media_type,
                    "integrity": _integrity_for(
                        task_id=task_id,
                        role=role,
                        repository=repository,
                        revision=revision,
                        remote_relative_path=remote_path,
                    ),
                }
            )
    elif role == "gold":
        repository = XLANG_REPOSITORY
        revision = XLANG_REVISION
        base_path = f"multi_apps/{source_task_id}"
        entries = []
        for expected_index, (
            (remote_name, media_type),
            logical_key,
        ) in enumerate(zip(gold_specs, gold_keys, strict=True)):
            remote_path = str(PurePosixPath(base_path) / remote_name)
            entries.append(
                {
                    "logical_key": logical_key,
                    "expected_index": expected_index,
                    "remote_relative_path": remote_path,
                    "path_status": "verified",
                    "path_evidence_ref": (
                        f"osworld:evaluator:{source_evaluator_id}:"
                        f"expected:{expected_index}"
                    ),
                    "purpose": "evaluator_gold",
                    "expected_media_type": media_type,
                    "integrity": _integrity_for(
                        task_id=task_id,
                        role=role,
                        repository=repository,
                        revision=revision,
                        remote_relative_path=remote_path,
                    ),
                }
            )
    else:
        raise OSWorldStateAssetDraftError("state manifest role 无效")

    return {
        "schema_version": 1,
        "manifest_id": f"{task_id}-{role}-draft-v1",
        "manifest_role": role,
        "draft_status": (
            "integrity_verified"
            if all(entry["integrity"]["status"] == "verified" for entry in entries)
            else "integrity_unverified"
        ),
        "distribution_policy": "download_only",
        "task_id": task_id,
        "task_uid": task_uid,
        "source_task_id": source_task_id,
        "source_evaluator_id": source_evaluator_id,
        "source_contract_sha256": source_contract_sha256,
        "source": {
            "provider": "huggingface_dataset",
            "repository": repository,
            "revision": revision,
        },
        "license": _license_for_repository(repository),
        "entries": entries,
    }


def _input_source(
    task_id: str,
    task_uid: str,
    source_task_id: str,
) -> tuple[str, str, str]:
    """解析 input 草案的固定 repository、revision 与 base path。

    输入参数：
        task_id/task_uid：canonical task identity。
        source_task_id：ArtifactEvidenceSpec 固定的 OSWorld source task。
    输出返回值：
        ``(repository, revision, remote base path)``；canonical tree 引用
        使用 Lee mirror，其余 direct URL 使用 OSWorld file cache。
    """

    return (
        XLANG_REPOSITORY,
        XLANG_REVISION,
        f"multi_apps/{source_task_id}",
    )


def _license_for_repository(repository: str) -> dict[str, Any]:
    """按已有版本化来源台账投影 repository 级许可证状态。

    输入参数：
        repository：两套固定 HF dataset identity 之一。
    输出返回值：
        xlang dataset 返回已有台账支持的 Apache-2.0/download-only；作者
        mirror 没有逐文件许可证据，显式返回 unverified/null。
    """

    if repository == XLANG_REPOSITORY:
        return {
            "status": "verified",
            "spdx_expression": "Apache-2.0",
            "evidence_ref": (
                "https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache"
            ),
            "distribution": "download_only",
        }
    raise OSWorldStateAssetDraftError("state asset repository 无效")


def _unverified_integrity() -> dict[str, Any]:
    """创建不声称已读取远端字节的完整性记录。

    输入参数：无。
    输出返回值：size、SHA 与 evidence 均为 ``null`` 的新字典。
    """

    return {
        "status": "unverified",
        "size_bytes": None,
        "sha256": None,
        "evidence_ref": None,
    }


def _integrity_for(
    *,
    task_id: str,
    role: str,
    repository: str,
    revision: str,
    remote_relative_path: str,
) -> dict[str, Any]:
    """投影固定 revision 下已核验或尚未核验的字节身份。

    输入参数：
        task_id：canonical ParaGUIBench 任务 ID。
        role：当前草案角色，必须为 ``input`` 或 ``gold``。
        repository/revision：生成不可变公开证据 URL 的 HF 来源身份。
        remote_relative_path：固定 revision 内的完整相对路径。
    输出返回值：
        authority 表命中时返回 verified size/SHA/evidence；否则返回字段均
        为 ``null`` 的 unverified 投影，绝不猜测远端字节。
    """

    verified = _VERIFIED_INTEGRITY.get(remote_relative_path)
    if verified is None:
        return _unverified_integrity()
    size_bytes, sha256 = verified
    return {
        "status": "verified",
        "size_bytes": size_bytes,
        "sha256": sha256,
        "evidence_ref": (
            "https://huggingface.co/datasets/"
            f"{repository}/resolve/{revision}/{remote_relative_path}"
        ),
    }


def _parse_arguments() -> argparse.Namespace:
    """解析 generate/check 子命令与仓库根。

    输入参数：无；读取当前进程参数。
    输出返回值：包含 ``command`` 与 ``repo_root`` 的 namespace。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args()


def main() -> int:
    """执行确定性生成或逐字节检查。

    输入参数：无；使用 ``_parse_arguments`` 的 CLI 参数。
    输出返回值：generate 成功或 check 一致返回 0；漂移返回 1。
    """

    arguments = _parse_arguments()
    root = arguments.repo_root.resolve()
    if arguments.command == "generate":
        write_osworld_state_asset_drafts(root)
        print("OSWorld state asset drafts generated: tasks=13; input=71; gold=15")
        return 0
    if check_osworld_state_asset_drafts(root):
        print("OSWorld state asset drafts valid: tasks=13; input=71; gold=15")
        return 0
    print("OSWorld state asset drafts drifted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
