#!/usr/bin/env python3
"""生成 Operation FileOperate 任务的固定 download-only 输入清单。

文件名与公开 builder 名保留最初 BatchOperation Office 纵向切片的
兼容身份；当前闭集还包含共用同一资产协议的 CombinationDocs 与
SearchAndWrite 输入。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path, PurePosixPath
from typing import Any


LEE_REPOSITORY = "leeLegendary/Parallel_benchmark"
LEE_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
# Excel-002 初始资产于 20260726 重制（取消预完成加粗/右对齐），以独立 commit 固定。
EXCEL002_LEE_REVISION = "b5f29e9cb725c80973af55f97b12fd279f066e3a"
XLANG_REPOSITORY = "xlangai/ubuntu_osworld_file_cache"
XLANG_REVISION = "711e0811642364e7aa8f10a8918367d0b626d578"
_MANIFEST_ROOT = PurePosixPath("benchmark/assets/manifests")
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_JPEG = "image/jpeg"
# 文本类型必须由公开路径后缀确定；不接受 libmagic 对
# Markdown 或 OOXML ZIP 容器的环境依赖性推断。
_TEXT = "text/plain"
_MARKDOWN = "text/markdown"
_CSV = "text/csv"
_HTML = "text/html"

# Excel-001/-003 的公开目录内容逐字节相同，仍由各自 UID 保留来源身份；
# Excel-002 的初始资产已重制（A3:C3 取消加粗、B4:C15 居中），不再与二者相同。
_REPAIRED_EXCEL002_WORKBOOKS = (
    (
        "store1.xlsx",
        5_632,
        "9fdb36b01e7c12835f080279b0666b2f7e6171eaa05617ef79f9a5d39ae008d7",
        _XLSX,
    ),
    (
        "store2.xlsx",
        5_641,
        "2850627275e5d78efbb26a95d959120218f5eae0a94add74e2f302693a053d1f",
        _XLSX,
    ),
    (
        "store3.xlsx",
        5_553,
        "dc95fc6f4daaa743d053c2a19705565b8e9e4a1ec87a3756af0be5e23f266b0b",
        _XLSX,
    ),
    (
        "store4.xlsx",
        5_551,
        "aecc7c83c35444753b130037322cb6f65fbf77482b90702742baa4f91141dd9f",
        _XLSX,
    ),
)

_COMMON_STORE_WORKBOOKS = (
    (
        "store1.xlsx",
        9_258,
        "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
        _XLSX,
    ),
    (
        "store2.xlsx",
        9_279,
        "23f584f69a818fe2dbc5e1dfcaa6ac103464edcb095c5ecf7de2ec50477ccd80",
        _XLSX,
    ),
    (
        "store3.xlsx",
        5_561,
        "cff0d19540c2e56c6355691c2ac41aafca059e6ce5aa2e9a79bffaa6c0b7c041",
        _XLSX,
    ),
    (
        "store4.xlsx",
        5_559,
        "683d3a4728beb8072649ae50babbf98b5a1a64e280ba2b0770bb264f23428fe7",
        _XLSX,
    ),
)

# tuple 顺序是公开固定 revision 目录的确定性字节闭集。
_TASK_ASSETS: dict[
    str,
    tuple[str, tuple[tuple[str, int, str, str], ...]],
] = {
    "Operation-FileOperate-BatchOperation-001": (
        "4b987de4-a022-4078-8f50-8f34a39115e6",
        (
            (
                "picture1.jpg",
                214_237,
                "96a704cf18e70183fe3f785e33fdd0a9459f7926357d41ed6866c403c7bce70d",
                _JPEG,
            ),
            (
                "picture2.jpg",
                44_543,
                "a37387c649a322536835366b86231ac2a6e4e704529ecb5240c9a7e29e69738c",
                _JPEG,
            ),
            (
                "picture3.jpg",
                927_632,
                "6962e09568bd9c9371a3058adc32866ed702ec2007aa93e141d1e8e1eee9e170",
                _JPEG,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationExcel-001": (
        "5e573e33-135a-4b45-b398-f85c0f7fea0a",
        _COMMON_STORE_WORKBOOKS,
    ),
    "Operation-FileOperate-BatchOperationExcel-002": (
        "a1510a05-9fca-46ba-b95d-451dd5779194",
        _REPAIRED_EXCEL002_WORKBOOKS,
    ),
    "Operation-FileOperate-BatchOperationExcel-003": (
        "fdb089b8-070f-4ccc-9612-e4599db799be",
        _COMMON_STORE_WORKBOOKS,
    ),
    "Operation-FileOperate-BatchOperationExcel-004": (
        "086f42e6-d412-4a4b-9702-0ef374e38c2b",
        (
            (
                "store1.xlsx",
                9_258,
                "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
                _XLSX,
            ),
            (
                "store2.xlsx",
                9_279,
                "56a523840a796142562fde92b67ddcaac247652d23e7376bac40299781b03457",
                _XLSX,
            ),
            (
                "store3.xlsx",
                9_211,
                "5eb434afffc3ccf5d903802b77be409ad0dd4dea73e6a2c738ceee17d939adbf",
                _XLSX,
            ),
            (
                "store4.xlsx",
                9_222,
                "5e3590b023745ffbfe26293061cddd45de5b5657380439b010a37bd89e539f67",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationExcel-005": (
        "cccf5baf-e392-47af-a605-65401ef56fe5",
        (
            (
                "store1.xlsx",
                9_258,
                "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
                _XLSX,
            ),
            (
                "store2.xlsx",
                9_278,
                "fe5bbc48c80cec38568b71a42508cb9df83a6c5b6388701445f1cf4170e3d1d8",
                _XLSX,
            ),
            (
                "store3.xlsx",
                9_211,
                "5eb434afffc3ccf5d903802b77be409ad0dd4dea73e6a2c738ceee17d939adbf",
                _XLSX,
            ),
            (
                "store4.xlsx",
                9_222,
                "5e3590b023745ffbfe26293061cddd45de5b5657380439b010a37bd89e539f67",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationExcel-006": (
        "ed04e7b5-a2c6-449a-a493-b22999919008",
        (
            (
                "KFC_Monthly_Data.xlsx",
                5_849,
                "4d9bcff171a5ae61bdb6b5c6b2b16a3d6fcb9af09b3ea639049b2c5457b68e1a",
                _XLSX,
            ),
            (
                "McDonalds_Monthly_Data.xlsx",
                5_858,
                "7c527377555479618e964962b756a7028564ed059f9273fbd16526b2170a6596",
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
                5_859,
                "d7c9ce0987a9c2b829d9943ead8894099b3b9664aeec4e2360b1bac3896750a2",
                _XLSX,
            ),
            (
                "Subway_Monthly_Data.xlsx",
                5_849,
                "0aeb94ba9eecf8135c6cfda2f83e8c7d9f4e40b102431a8c55e4c315cfd4f898",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationExcel-007": (
        "7e6bdc0a-b1dd-47c5-98a7-baafd3f5fd0f",
        (
            (
                "Company_Sales_Data.xlsx",
                6_944,
                "f5ce5597c021c0cd118b0b2bf4a96836baf9c87cbc4bb50de7b78e6a5abe7d88",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationExcel-009": (
        "0f045849-d0e4-48d5-9010-ece2534c2b8c",
        (
            (
                "Company_Invoices.xlsx",
                6_068,
                "c8cc204b631b1640ff6f7c9a62c9051e843c2cd0c2bc8c201e022753ac852c5e",
                _XLSX,
            ),
            (
                "Electronics_Orders.xlsx",
                5_626,
                "306733299e609a65570a3e066222176dfeee2c6a1756a4360e272ce3ad041016",
                _XLSX,
            ),
            (
                "Food_Delivery_Orders.xlsx",
                5_859,
                "93847919ad9b11fc5a478f47c0ec48c12cd55b1896ede98c66d2c19e3554a571",
                _XLSX,
            ),
            (
                "Hotel_Bookings.xlsx",
                5_686,
                "360c2ef6edcab10fed2a6a1413441ce3db87755c05ad9c4d8e1b0488cb1e2bf2",
                _XLSX,
            ),
            (
                "Warehouse_Shipments.xlsx",
                5_770,
                "edc654d7e0c31621d80d9f9d3f8a97b2f03a10488b94c63004c68bfa19083b32",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationPPT-001": (
        "a7ba9165-a65f-45a3-8449-ac2f358d3a9d",
        (
            (
                "ML.pptx",
                37_695,
                "e044e7ebeafd18dbe789a346cb95f2a1a230b62c6330ae19f0e9517921a6f241",
                _PPTX,
            ),
            (
                "The source of AI.pptx",
                37_433,
                "f342a5152b2cfca9cc3117f6b5a681ad56e662fce0e0e3bb84af63ab960da11d",
                _PPTX,
            ),
            (
                "welcome.pptx",
                37_657,
                "77bafb3ac9bc92d5fdd287b2d9987d814b8bb87f90402cc3fbc8b4a4a438c6c8",
                _PPTX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationPPT-002": (
        "85da5285-4ba1-4550-8f5a-00ea07fca510",
        (
            (
                "beijing.pptx",
                36_477,
                "7eeb0abd901c78c2d304e9d49fdd47399a1ccd9e3a85f8b6d749703f23b6d83a",
                _PPTX,
            ),
            (
                "introduction.pptx",
                35_876,
                "f40cda30c0fac98d2520acb2b223b5c9582c97fa78fcf5ebb630a374e6ed1459",
                _PPTX,
            ),
            (
                "powerPoint.pptx",
                35_333,
                "f066b77602a6713991feb718c91154f8f4fd8ddf81e3d4f5895481a0bc095e54",
                _PPTX,
            ),
            (
                "traveling.pptx",
                38_339,
                "7fa356e17150dd881b9a86673de6d027af75cd6450384fc81d9ee49a3a040399",
                _PPTX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-001": (
        "1dd4c724-6930-42ae-b9c6-d219083f3480",
        (
            (
                "2026 Q1 Product Development Plan.docx",
                16_844,
                "238f5fe6b9445cf7e0a977e380bd253ae93e9731f5b30cc0cc88d2d0d1eb9597",
                _DOCX,
            ),
            (
                "Application of Deep Reinforcement Learning in Robotic Arm Control.docx",
                16_053,
                "578ff6efb57c6ba29c425fca6b2da7590d2bc54a0eed9284646077275923a288",
                _DOCX,
            ),
            (
                "Remote Work Policy v2.0.docx",
                16_244,
                "3d11f5639803d2223fb4ba51c08f33d2d05f7c7c42f143ea337d9506a9505f3d",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-002": (
        "f9d27527-85a2-4b64-99e9-7f3199cb1cd5",
        (
            (
                "apology_letter.docx",
                14_428,
                "4c80b5f0459bcb376b912263d771bb28d8fca37c34aab2111514c2f0a01b1dd8",
                _DOCX,
            ),
            (
                "climite_news.docx",
                14_503,
                "1cdc95c06e69d95da53466bbd0ef576cf4bcfd8a0dc1dac80424b0864c32c559",
                _DOCX,
            ),
            (
                "project_update.docx",
                14_470,
                "848cc633ce83034440b34afb075be11d88f802479717104c2ecce929038f6bc0",
                _DOCX,
            ),
            (
                "sci-fi_narrative.docx",
                14_372,
                "f56462942a5160e5f61913fd4db48160cd0c5252aa88d062c0ed1f502c2ab0bb",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-003": (
        "c36d8396-4dc7-4390-a661-9bb8c54bee9f",
        (
            (
                "test3_txt1.docx",
                13_832,
                "166f10b282d16f2d43f2ec9e08a4e64e7d407b7135bedb6cda871cafa1388fbb",
                _DOCX,
            ),
            (
                "test3_txt2.docx",
                13_725,
                "be1ea1ac473b75cb1b93c6c09df991edf153d48e604527f32574b8e2ab7f21d9",
                _DOCX,
            ),
            (
                "test3_txt3.docx",
                13_736,
                "1f3521b2d337c2ef24a97e9c900d223bfee658553dbacef2abfa348146089468",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-004": (
        "6ed5298a-16d6-44fb-9c0c-35e87d3f13c0",
        (
            (
                "center.docx",
                13_836,
                "0ea61b19aab35f065fc76e99885d16ece502236d443f7ca78a71319dec8ae2ee",
                _DOCX,
            ),
            (
                "episode.docx",
                13_840,
                "0dba0922c2a836cdd96ce9e2a9a748d6a63cee63490894db3f53c71baaa2f3c5",
                _DOCX,
            ),
            (
                "experience.docx",
                13_861,
                "4157efd87ca21a8b2d580eeeb98a7e975cd21347f3fe19603b202c5c658f0db3",
                _DOCX,
            ),
            (
                "hall.docx",
                13_799,
                "94e39cdc999f5824e17dd30a8739bf7daa8411d1a66f37aca1361c148c5e6da7",
                _DOCX,
            ),
            (
                "travel.docx",
                13_919,
                "90b65ad748a328c000fde15af24bbf95b862790d02744b5cd585ae96263189d5",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-005": (
        "0ae169d8-aeb9-4c78-ab11-a182444c8eed",
        (
            (
                "Introduction to Artificial Intelligence.docx",
                16_208,
                "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
                _DOCX,
            ),
            (
                "The Quiet Station.docx",
                16_333,
                "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
                _DOCX,
            ),
            (
                "The Silent Library.docx",
                14_323,
                "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-006": (
        "186a98aa-ada2-44e3-9187-558ddee9153b",
        (
            (
                "2025年重点城市房地产市场活跃度分析.docx",
                14_400,
                "8e84ae9cfcb2af690e0fb84c8123d576245e2f899abba84991dbb29ff1762bd5",
                _DOCX,
            ),
            (
                "城市平均房价.docx",
                13_774,
                "35c786bad594c07926148d9051297c5344a0c5a22ac5236e0b4fa344f3f16eaa",
                _DOCX,
            ),
            (
                "设备名称单价.docx",
                13_944,
                "882061bf39f85b9675468589a074d529f05130ddf72902415a81e45206d1f5fc",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-007": (
        "8ff7afec-e238-43df-a240-c4d16807f8b4",
        (
            (
                "AI.docx",
                13_715,
                "9724e04b41145d924056bebe533f626878c94da45194e383e72e44a72ea78fc4",
                _DOCX,
            ),
            (
                "agent.docx",
                13_800,
                "7956888a572709ac39587bcf15ca71f501e4b18f2ee47770be5a8f8a4c80dfb4",
                _DOCX,
            ),
            (
                "education.docx",
                13_694,
                "595db8729b780e3384ad09ba3ef82ee08c889fba358ae8d777a50bcf57c1a620",
                _DOCX,
            ),
            (
                "idea.docx",
                13_696,
                "c0acfafaec0c8eaf42c9a6bb89444079c6b7a42124e90437d269f6bf48f21d3f",
                _DOCX,
            ),
            (
                "software engineering.docx",
                13_828,
                "77e304f07b5b8fe2d745bda739f1f40bec708a8f87b57a33f881ec762e497921",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-008": (
        "b9929f12-d179-450a-922a-22afb361bad3",
        (
            (
                "Introduction to Artificial Intelligence.docx",
                16_208,
                "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
                _DOCX,
            ),
            (
                "The Quiet Station.docx",
                16_333,
                "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
                _DOCX,
            ),
            (
                "The Silent Library.docx",
                14_323,
                "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
                _DOCX,
            ),
            (
                "software engineering.docx",
                13_828,
                "77e304f07b5b8fe2d745bda739f1f40bec708a8f87b57a33f881ec762e497921",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-009": (
        "6af0b589-eec2-4b76-a0dd-b18a06ff705b",
        (
            (
                "Introduction to Artificial Intelligence.docx",
                16_208,
                "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
                _DOCX,
            ),
            (
                "Research on Multi.docx",
                14_102,
                "b1300366fab543621dd388c752a83deaf3f0f8fee704655766369ae88cabc230",
                _DOCX,
            ),
            (
                "The Quiet Station.docx",
                16_333,
                "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
                _DOCX,
            ),
            (
                "The Silent Library.docx",
                14_323,
                "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-010": (
        "6e55deaf-c95d-49e9-91b2-b0155fe1dc45",
        (
            (
                "Cats.docx",
                13_874,
                "8ac5b07a61c07cb8f7774d17497a08556786a5df0e5f9f8a01e57f4fa0935503",
                _DOCX,
            ),
            (
                "Dogs.docx",
                13_971,
                "e140ed48d16d4d970419e9ed60f0afd6305575646056bbd1ab7aa2786e40010e",
                _DOCX,
            ),
            (
                "Foxes.docx",
                13_955,
                "c0cfdacf3dff8f4804b6767cd8448f3155d58909bd8330cb2e658a7adb746de2",
                _DOCX,
            ),
            (
                "Hamsters.docx",
                13_898,
                "711688f693e014a1172af1fa3e27f7128bd5eb6483dca25de9ee5ee3363bcd5d",
                _DOCX,
            ),
            (
                "Tigers.docx",
                13_938,
                "13889f2886526779bf391a39258f2bba495a04ff04a5409334636cb475754be1",
                _DOCX,
            ),
            (
                "images/Cats.jpeg",
                5_841,
                "516a5dc48b50aaf03bd7aeb3f9fd0f20de44d624c9e8f9de66b46d92a36db5b5",
                _JPEG,
            ),
            (
                "images/Dogs.jpeg",
                8_111,
                "13d12502a8c626efbf4dc053f73f2c56a7c3de8955d26d2c7fe2bb9282cbd17a",
                _JPEG,
            ),
            (
                "images/Foxes.jpeg",
                7_257,
                "5bbc110037d4e937516295531e53cf8dac0f7d4a72100d8457a8f1064a0b643d",
                _JPEG,
            ),
            (
                "images/Hamsters.jpeg",
                5_900,
                "c4bc248ca159adc2278a62ccab8314222d22c7378848186468da507532a34469",
                _JPEG,
            ),
            (
                "images/Tigers.jpeg",
                10_111,
                "6efffea249b289eb42e416eb67257b894d5f2e8f1ca33949cdd9c0dd1af2a5d4",
                _JPEG,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-011": (
        "248add77-e3c1-4a59-b98e-03752238dc81",
        (
            (
                "Doc_A.docx",
                14_149,
                "9d94b0b9a42da4f9fb467dc6b108d0b434e16588cdc7bf4ee7b9e052ce57b8bc",
                _DOCX,
            ),
            (
                "Doc_B.docx",
                13_989,
                "b37273f61005229c898d38f19a082213f68a74bcdb24ec8cf511fd32678411f8",
                _DOCX,
            ),
            (
                "Doc_C.docx",
                13_957,
                "0d8dc5e595bd47d9a56ffe197b7d70e2c7417ed7eabf579127188925d42d177f",
                _DOCX,
            ),
            (
                "Doc_D.docx",
                13_972,
                "b52a5eb1d88c27354e27d766c5a1b50af94337d7a71032fce1a5fca714c25644",
                _DOCX,
            ),
            (
                "Doc_E.docx",
                13_959,
                "c1b56a1e389757e664a7b9658a2f143671b75b08b02511279621310e9dbc8088",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-BatchOperationWord-012": (
        "0857689f-8976-49a3-9314-d2b194f9d629",
        (
            (
                "Clinical Procedure.docx",
                13_971,
                "ccbec2ce1c0ea1df920f08676d3b9bf42b9397543b0d013b8a0f5416cfc40e08",
                _DOCX,
            ),
            (
                "Hardware Review.docx",
                13_998,
                "2fdde89b1789626f2e71826b1a0acf1260a54c620597273dcf30d6fb7f53223a",
                _DOCX,
            ),
            (
                "Infrastructure Log.docx",
                14_071,
                "51378bf4bb9058631f40a155226d7403425166cca1740aace5d016656943e1e0",
                _DOCX,
            ),
            (
                "Security Protocol.docx",
                13_934,
                "02718839e2eb6681c092ff1b2347eb0ce83047772332890f0bd9a435c94ca1ad",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-001": (
        "c2ec79c0-bb7c-4f45-b5e5-437d15d518cb",
        (
            (
                "KFC_Monthly_Data.xlsx",
                5_849,
                "4d9bcff171a5ae61bdb6b5c6b2b16a3d6fcb9af09b3ea639049b2c5457b68e1a",
                _XLSX,
            ),
            (
                "McDonalds_Monthly_Data.xlsx",
                5_858,
                "7c527377555479618e964962b756a7028564ed059f9273fbd16526b2170a6596",
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
                5_859,
                "d7c9ce0987a9c2b829d9943ead8894099b3b9664aeec4e2360b1bac3896750a2",
                _XLSX,
            ),
            (
                "Subway_Monthly_Data.xlsx",
                5_849,
                "0aeb94ba9eecf8135c6cfda2f83e8c7d9f4e40b102431a8c55e4c315cfd4f898",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-003": (
        "2654f880-dd6b-4f8c-9f88-aebe2bfa51be",
        (
            (
                "McDonalds_Monthly_Data.xlsx",
                9_545,
                "ce00b8df3c48ebb8711a477af2de10053affe0e4a2327c485e8d93ea6ad86e5d",
                _XLSX,
            ),
            (
                "McDonalds_powerpoint_report.pptx",
                41_099,
                "c30c3cfeee0c32dd80ea06d54f36d46237af325f4491c975d6d2464b0d08fcc0",
                _PPTX,
            ),
            (
                "store1.xlsx",
                9_258,
                "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
                _XLSX,
            ),
            (
                "store2.xlsx",
                9_278,
                "fe5bbc48c80cec38568b71a42508cb9df83a6c5b6388701445f1cf4170e3d1d8",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-004": (
        "04a0b25e-f726-40a0-a88d-69bbf538f634",
        (
            (
                "McDonald_finacial_report.docx",
                14_355,
                "ef78a03c87b452e6c29c1e1fc317ee375fff311b5b7cc8b38b84e0f49ddf10a6",
                _DOCX,
            ),
            (
                "McDonalds_powerpoint_report.pptx",
                217_737,
                "997c1a757865abba05c568fa4249629905ae75cccc48d9837225f23c486559ea",
                _PPTX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-005": (
        "58870403-ea44-4f4c-8941-b2a57f170cd1",
        (
            (
                "Business_Report.txt",
                1_175,
                "f9c53ba0b46d5eb4f2141c0f7ef21f83e2e3820fd0220155082a37803031f1a0",
                _TEXT,
            ),
            (
                "Development_Guide.md",
                3_147,
                "7cf1463ce297ba1cabcd07c5b79fc132e72810c2dc6a278abb3fbf7d139eb678",
                _MARKDOWN,
            ),
            (
                "Employee_Directory.csv",
                4_274,
                "7231ba8f905a57726ef5ebbd722be4308c31a776572458cd93ca36c367178ba4",
                _CSV,
            ),
            (
                "Product_Catalog.html",
                3_228,
                "b4aab3be17aa747ae6d4ed0fe52909e75b7d43843541d493e2073de9df02c346",
                _HTML,
            ),
            (
                "Training_Program.docx",
                37_740,
                "d71ba36cf46912b1c708810bbb85129f974a484d522af32e48e5ff2b84ace514",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-006": (
        "d5999c0f-ff61-476d-8e98-9c5f1b91fed9",
        (
            (
                "Conference.pptx",
                37_017,
                "f3d71a2212039c93928883a1ce059679d29db9f34670b6d28e64868a596d2803",
                _PPTX,
            ),
            (
                "Eval_framework.docx",
                14_364,
                "737f13011a878a92740c8781d370dfbb3505509d8c0b1b770a5f974cea9e7f66",
                _DOCX,
            ),
            (
                "Presentation_Strategy.pptx",
                38_013,
                "e461cb2eb21edeb7f279b9643d304d6115820fca42d2d6bc146085cb631d98fc",
                _PPTX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-007": (
        "eebc7ed2-5c7d-4df5-ab71-b53040167536",
        (
            (
                "Conference.pptx",
                38_079,
                "38877fc03a1c54f2a3066b347d0a09114c312ba0e63e545c85f520e7d76eef0a",
                _PPTX,
            ),
            (
                "Eval_framework.docx",
                14_364,
                "737f13011a878a92740c8781d370dfbb3505509d8c0b1b770a5f974cea9e7f66",
                _DOCX,
            ),
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
            (
                "Presentation_Strategy.pptx",
                38_013,
                "e461cb2eb21edeb7f279b9643d304d6115820fca42d2d6bc146085cb631d98fc",
                _PPTX,
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-008": (
        "3f600f5d-a835-4c59-9fae-b9139365d03e",
        (
            (
                "GUI Benchmark Study.docx",
                13_779,
                "a24dcc85a88e547af1cc4753f34deea75f5a67c4a1f5e50a7162601d66b6cd09",
                _DOCX,
            ),
            (
                "Multi Modal Agent.docx",
                13_762,
                "5eb752b06f039498aa1e5061e078e2b58ca600e0e3cadfe10c60df4a0cab6ebe",
                _DOCX,
            ),
            (
                "Naming_rules.txt",
                911,
                "dba136f226431c75e2d9b5ad2cf580d86dccb318b7a5e15a71cc0797fe9668c3",
                _TEXT,
            ),
            (
                "Parallel Execution.docx",
                13_771,
                "ed8759887f7f12b04981dc6bc713e0ecc32eb81696de5bfdc2ebb40548127663",
                _DOCX,
            ),
            (
                "Project_Information.xlsx",
                8_899,
                "26b97f507b0bf0958b9bb9dd9c2fe0a221312489bdc5f6dced56d03ddce39c21",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-002": (
        "31d84d8d-8c61-4181-b321-44b83adc03f9",
        (
            (
                "company_info.xlsx",
                5_016,
                "fa428d1185fb7b8a02420b09f08e12fb65e0da233815b413f5dc26a7717388f5",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-004": (
        "19ed62a3-9df0-4879-a685-0681acc1c708",
        (
            (
                "Conferences_details.xlsx",
                9_019,
                "c8f86d189f80c9d74281d657b42b65936f64b008abd62edb3e3650f2333bb9c5",
                _XLSX,
            ),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-006": (
        "d624fd2f-f184-4041-a33c-678f6fa10744",
        (
            (
                "The development of LLMs.docx",
                15_351,
                "b665ba35a38be8c3ec87f9f16dd8fa7bd2dfdd812802e3c4e223d116cf6a0ed0",
                _DOCX,
            ),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-007": (
        "6f137b26-6efd-45e7-bd3c-034c81ddc790",
        (
            (
                "Conference.xlsx",
                9_235,
                "955f438f95a176a5d8e96ed3ec32ac11924ad44dc68c7e4d2480f10fe4fd4bab",
                _XLSX,
            ),
        ),
    ),
}

# 只有 SearchAndWrite-007 来自 OSWorld 文件缓存；其他任务仍必须
# 精确绑定 Lee 仓库与 revision，不允许以宽泛来源 schema 混用。
_TASK_SOURCE_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "Operation-FileOperate-BatchOperationExcel-002": (
        LEE_REPOSITORY,
        EXCEL002_LEE_REVISION,
        "benchmark_dataset/a1510a05-9fca-46ba-b95d-451dd5779194",
    ),
    "Operation-FileOperate-SearchAndWrite-007": (
        XLANG_REPOSITORY,
        XLANG_REVISION,
        "multi_apps/6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a",
    ),
}


class BatchOperationOfficeAssetError(RuntimeError):
    """表示固定任务身份、canonical 绑定或 manifest 闭集漂移。"""


def manifest_relative_path(task_id: str) -> str:
    """返回一个固定任务的 manifest 仓库相对路径。

    输入参数：
        task_id：必须属于本生成器冻结闭集的 canonical ID。
    输出返回值：
        使用 POSIX 分隔符的 manifest JSON 仓库相对路径。
    异常：
        BatchOperationOfficeAssetError：任务不属于冻结闭集。
    """

    if task_id not in _TASK_ASSETS:
        raise BatchOperationOfficeAssetError("BatchOperation Office task 身份无效")
    return str(_MANIFEST_ROOT / f"{task_id}.json")


def build_batch_operation_office_asset_manifests(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """从冻结元数据和 canonical 身份构造确定性 manifest。

    输入参数：
        repo_root：包含 `benchmark/tasks` 的 ParaGUIBench 仓库根。
    输出返回值：
        manifest 相对路径到 JSON object 的映射。
    异常：
        BatchOperationOfficeAssetError：仓库根、UID 或 canonical 资产模式漂移。
    """

    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise BatchOperationOfficeAssetError("BatchOperation Office repo root 无效")
    documents: dict[str, dict[str, Any]] = {}
    file_total = 0
    for task_id in sorted(_TASK_ASSETS, key=lambda value: value.encode("utf-8")):
        task_uid, files = _TASK_ASSETS[task_id]
        relative_path = manifest_relative_path(task_id)
        task = _load_canonical_task(repo_root, task_id)
        if task.get("task_uid") != task_uid:
            raise BatchOperationOfficeAssetError("BatchOperation Office task UID 漂移")
        if task.get("asset_manifest") != relative_path or any(
            field in task
            for field in ("prepare_script_path", "prepare_exclude_patterns")
        ):
            raise BatchOperationOfficeAssetError(
                "BatchOperation Office canonical 资产绑定漂移"
            )
        documents[relative_path] = _build_manifest(task_id, task_uid, files)
        file_total += len(files)
    if len(documents) != 34 or file_total != 128:
        raise BatchOperationOfficeAssetError(
            "BatchOperation Office manifest 数量闭包漂移"
        )
    return documents


def serialize_asset_manifest(document: dict[str, Any]) -> bytes:
    """把 builder 返回的 manifest 编码为唯一 JSON 字节。

    输入参数：
        document：`build_batch_operation_office_asset_manifests` 的单份结果。
    输出返回值：
        两空格缩进、保留 Unicode、末尾单换行的 UTF-8 字节。
    """

    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check_batch_operation_office_asset_manifests(repo_root: Path) -> bool:
    """逐字节检查三十四份正式 manifest 与确定性 builder 一致。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        三十四份 manifest 都存在且字节精确一致时返回 `True`；
        canonical 漂移、文件缺失或字节不同时返回 `False`。
    """

    try:
        documents = build_batch_operation_office_asset_manifests(repo_root)
    except BatchOperationOfficeAssetError:
        return False
    for relative_path, document in documents.items():
        try:
            actual = (repo_root / relative_path).read_bytes()
        except OSError:
            return False
        if actual != serialize_asset_manifest(document):
            return False
    return True


def write_batch_operation_office_asset_manifests(repo_root: Path) -> None:
    """将 builder 的三十四份确定性 manifest 写入正式路径。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；每个目标只写入 builder 的唯一 UTF-8 字节。
    """

    for relative_path, document in build_batch_operation_office_asset_manifests(
        repo_root
    ).items():
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(serialize_asset_manifest(document))


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析 generator/check 子命令和仓库根。

    输入参数：
        argv：可选的命令行参数；`None` 时读取当前进程参数。
    输出返回值：
        包含 `command` 与 `repo_root` 的 argparse namespace。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """执行确定性生成或逐字节检查。

    输入参数：
        argv：可选参数序列，便于测试不依赖全局 `sys.argv`。
    输出返回值：
        生成或检查成功返回 0；检查漂移返回 1。
    """

    arguments = _parse_arguments(argv)
    repo_root = arguments.repo_root.resolve()
    if arguments.command == "generate":
        write_batch_operation_office_asset_manifests(repo_root)
        print("BatchOperation Office asset manifests generated: tasks=34; files=128")
        return 0
    if check_batch_operation_office_asset_manifests(repo_root):
        print("BatchOperation Office asset manifests valid: tasks=34; files=128")
        return 0
    print("BatchOperation Office asset manifests drifted")
    return 1


def _load_canonical_task(repo_root: Path, task_id: str) -> dict[str, Any]:
    """读取并确认一份冻结 canonical task 的内外身份。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
        task_id：决定文件名的固定 canonical ID。
    输出返回值：
        顶层为 JSON object 且内部 task_id 一致的任务。
    异常：
        BatchOperationOfficeAssetError：文件不可读、JSON 无效或身份不一致。
    """

    try:
        task = json.loads(
            (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        raise BatchOperationOfficeAssetError(
            "BatchOperation Office canonical task 无法读取"
        ) from None
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise BatchOperationOfficeAssetError(
            "BatchOperation Office canonical task 身份漂移"
        )
    return task


def _build_manifest(
    task_id: str,
    task_uid: str,
    files: tuple[tuple[str, int, str, str], ...],
) -> dict[str, Any]:
    """由固定任务身份与逐文件字节元数据构造 manifest。

    输入参数：
        task_id：manifest 的 asset_set_id。
        task_uid：固定 Hugging Face 目录的 UID。
        files：按 UTF-8 路径顺序固定的 path/size/SHA-256/MIME 元组。
    输出返回值：
        `unverified`/`download_only` 边界与任务专属固定来源的
        manifest object。
    """

    repository, revision, base_path = _source_identity(task_id, task_uid)

    return {
        "schema_version": 1,
        "asset_set_id": task_id,
        "source": {
            "provider": "huggingface_dataset",
            "repository": repository,
            "revision": revision,
            "base_path": base_path,
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": path,
                "size": size,
                "sha256": sha256,
                "media_type": media_type,
            }
            for path, size, sha256, media_type in files
        ],
    }


def _source_identity(task_id: str, task_uid: str) -> tuple[str, str, str]:
    """返回任务专属且不可混用的固定来源身份。

    输入参数：
        task_id：canonical 任务 ID，用于选择显式的非 Lee 来源。
        task_uid：canonical 任务 UID，Lee 任务使用其构造固定目录。
    输出返回值：
        ``repository``、``revision`` 与 ``base_path`` 三元组；
        未显式覆盖的任务只能使用固定 Lee 来源。
    """

    return _TASK_SOURCE_OVERRIDES.get(
        task_id,
        (LEE_REPOSITORY, LEE_REVISION, f"benchmark_dataset/{task_uid}"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
