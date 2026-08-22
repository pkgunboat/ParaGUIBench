"""QA 评价链共享的答案标签提取工具。"""

from __future__ import annotations

import re
from typing import Optional


def extract_last_complete_answer_tag(text: Optional[str]) -> Optional[str]:
    """提取文本中最后一个完整的 ``<answer>`` 标签内容。

    功能：统一 PlanAgent、QA pipeline 与其他上游组件的“最终答案”语义；允许
    标签大小写和跨行内容，忽略末尾不完整标签，并保留最后一个完整空标签为
    空字符串，防止错误回退到较早草稿。
    输入参数：text，可能包含零个或多个 answer 标签的模型输出；None 视为空。
    输出返回值：存在完整标签时返回最后一个标签的去空白内容；不存在时返回
    None。
    """
    if not text:
        return None
    matches = re.findall(
        r"<answer>(.*?)</answer>",
        str(text),
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not matches:
        return None
    return matches[-1].strip()
