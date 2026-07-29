"""QA 最终答案标签提取的公开行为测试。"""

from __future__ import annotations

from paraguibench.evaluation.answers import extract_last_complete_answer


def test_last_complete_answer_tag_wins_without_falling_back_from_empty() -> None:
    """验证最后一个完整空标签覆盖早期草稿，尾部残缺标签不参与选择。

    输入参数：
        无；测试数据包含早期答案、最后完整空标签及一个未闭合草稿。
    输出返回值：
        无；公开提取接口必须返回空字符串，不能回退到早期答案。
    """

    output = "<answer>draft</answer><ANSWER>  </ANSWER><answer>unfinished"

    assert extract_last_complete_answer(output) == ""
