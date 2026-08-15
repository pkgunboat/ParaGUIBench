"""跨 OSWorld evidence 与纯评价层共享的不可变状态 contract。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChromeProfileNameObservation:
    """保存单台 VM 的 Chrome profile 名称读取结果。

    输入参数：
        profile_name：Preferences 中 ``profile.name`` 的值；字段不存在时
            允许为 ``None``。
        complete：是否已完整、无歧义地读取并解析固定 profile 文件。
    输出返回值：
        不可变 observation；对象本身不应持久化到 RunStore。
    """

    profile_name: str | None
    complete: bool = True


@dataclass(frozen=True, slots=True)
class GoogleShoppingActiveTabObservation:
    """保存单台 VM 在同一时点采集的 Google Shopping 活动页状态。

    输入参数：
        url：活动标签页完整 URL。
        locale：页面或浏览器报告的 locale。
        filter_surface_observed：是否看见可解释的筛选控件表面。
        selection_enumeration_complete：是否能闭集枚举全部已选筛选。
        selection_evidence：形成完整性结论的固定 adapter 证据标识。
        selected_filter_labels：同一快照内确认选中的筛选标签。
        blocked_reason：可信探针识别的 consent/captcha 阻塞原因。
    输出返回值：
        不可变 observation；URL 与标签仅驻留 evaluator 可信内存。
    """

    url: str
    locale: str
    filter_surface_observed: bool
    selection_enumeration_complete: bool
    selection_evidence: str
    selected_filter_labels: tuple[str, ...]
    blocked_reason: str = ""
