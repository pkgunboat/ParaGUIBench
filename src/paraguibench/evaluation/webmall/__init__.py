"""WebMall 评价器公开入口。"""

from paraguibench.evaluation.webmall.url_set import (
    WebMallURLSetEvaluation,
    evaluate_webmall_url_set,
)

__all__ = ["WebMallURLSetEvaluation", "evaluate_webmall_url_set"]
