"""OSWorld active-tab result schema 的轻量兼容层。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Tuple
import unicodedata
from urllib.parse import parse_qs, urlsplit


class ActiveTabEvaluatorError(RuntimeError):
    """表示活动页无法可靠观测或 result 配置本身无效。"""


@dataclass(frozen=True)
class ActiveTabSnapshot:
    """保存一次活动浏览器标签页读取所得的不可变状态。

    功能：作为同一 OSWorld 多指标评价共享的状态边界，避免各指标在
    不同时刻重新读取浏览器而产生相互矛盾的结果。
    输入参数：
        url: 抓取时活动标签页的完整 URL。
        page_kind: 活动页分类；Google Shopping 使用
            ``google_shopping``，其它页面使用 ``other``。
        locale: 页面报告的语言区域标识。
        filter_surface_observed: 是否观察到可解释的筛选控件表面。
        selection_enumeration_complete: 是否有闭集证据证明全部已选筛选项
            均已枚举；只有该字段为真时才能断言不存在额外筛选。
        selection_evidence: 产生完整性结论的 adapter 证据标识，供 live
            门禁和错误诊断使用。
        selected_filter_labels: 通过语义状态确认已选中的筛选标签。
    输出返回值：
        不可变的 ``ActiveTabSnapshot`` 实例。
    """

    url: str
    page_kind: str = "other"
    locale: str = ""
    filter_surface_observed: bool = False
    selection_enumeration_complete: bool = False
    selection_evidence: str = ""
    selected_filter_labels: Tuple[str, ...] = ()


def normalize_filter_label(label: str) -> str:
    """规范化筛选标签中的纯布局差异。

    功能：执行 Unicode NFKC、将常见 Unicode dash 统一为 ASCII
    hyphen，并把 NBSP/连续空白压缩为单空格。函数刻意不做大小写折叠、
    分词或模糊匹配，避免把语义不同的筛选误判为 gold。
    输入参数：
        label: 从浏览器语义控件读取的原始标签文本。
    输出返回值：
        仅消除排版差异后的稳定标签。
    """

    normalized = unicodedata.normalize("NFKC", str(label))
    normalized = re.sub(
        r"[\u2010\u2011\u2012\u2013\u2014\u2212]",
        "-",
        normalized,
    )
    normalized = re.sub(
        (
            r"(?P<left>(?:[$€£¥]\s*)?\d+(?:[.,]\d+)?)"
            r"\s*-\s*"
            r"(?P<right>(?:[$€£¥]\s*)?\d+(?:[.,]\d+)?)"
        ),
        r"\g<left> - \g<right>",
        normalized,
    )
    return " ".join(normalized.split())


def _project_selected_filters(
    snapshot: ActiveTabSnapshot,
    result_config: Dict[str, Any],
) -> Dict[str, bool]:
    """把已选筛选集合投影为旧 OSWorld 的平面布尔字典。

    功能：兼容 ``class_multiObject_search_exist`` 配置；配置中的
    class 名只作为历史定位提示，值列表才定义需要检查的标签。
    ``is_other_exist`` 是集合外额外已选项的哨兵，不是页面文本。
    输入参数：
        snapshot: 同次评价共享的活动页快照。
        result_config: 原 OSWorld ``active_tab_html_parse`` 配置。
    输出返回值：
        以原始 gold 标签为键的布尔字典。
    异常：
        schema 缺失或标签列表无效时抛出 ``ValueError``。
    """

    schema = result_config.get("class_multiObject_search_exist")
    if not isinstance(schema, dict) or not schema:
        raise ValueError(
            "active_tab_html_parse 缺少 class_multiObject_search_exist"
        )

    configured_labels = []
    for labels in schema.values():
        if not isinstance(labels, list):
            raise ValueError(
                "class_multiObject_search_exist 的标签必须是列表"
            )
        configured_labels.extend(str(label) for label in labels)

    expected_labels = [
        label for label in configured_labels if label != "is_other_exist"
    ]
    projected = {
        label: False
        for label in expected_labels
    }
    if "is_other_exist" in configured_labels:
        projected["is_other_exist"] = False

    if snapshot.page_kind != "google_shopping":
        return projected

    query = parse_qs(urlsplit(snapshot.url).query)
    has_search_query = any(
        str(value).strip()
        for value in query.get("q", [])
    )
    if not has_search_query:
        # 停在 Shopping 首页或空查询页是明确的 Agent 状态错误。此时
        # URL 子指标也必然失败，不应因页面尚无筛选面而升级为评价器故障。
        return projected

    if not snapshot.locale.lower().replace("_", "-").startswith("en"):
        raise ActiveTabEvaluatorError(
            f"不支持的 Google Shopping locale: {snapshot.locale or '<empty>'}"
        )
    if not snapshot.filter_surface_observed:
        raise ActiveTabEvaluatorError(
            "Google Shopping 筛选控件表面不可观测"
        )
    if not snapshot.selection_enumeration_complete:
        evidence = snapshot.selection_evidence or "<empty>"
        raise ActiveTabEvaluatorError(
            "Google Shopping 已选筛选枚举不完整，"
            f"不能可靠判断 is_other_exist；evidence={evidence}"
        )

    selected_labels = (
        {
            normalized
            for label in snapshot.selected_filter_labels
            if (normalized := normalize_filter_label(label))
        }
        if snapshot.page_kind == "google_shopping"
        else set()
    )
    expected_by_normalized = {
        normalize_filter_label(label): label
        for label in expected_labels
    }
    expected_set = set(expected_by_normalized)
    projected.update({
        label: normalize_filter_label(label) in selected_labels
        for label in expected_labels
    })
    if "is_other_exist" in configured_labels:
        projected["is_other_exist"] = bool(selected_labels - expected_set)
    return projected


class ActiveTabResultProvider:
    """将活动页快照投影为 OSWorld evaluator.result 所需的数据。"""

    def __init__(
        self,
        snapshot_loader: Callable[[], ActiveTabSnapshot],
    ) -> None:
        """保存延迟快照读取函数。

        功能：构造 active-tab result provider；当前仅在请求 result 时
        调用 loader，以免普通非 active-tab 任务触发浏览器依赖。
        输入参数：
            snapshot_loader: 无参数函数，返回一次活动页快照。
        输出返回值：
            无；初始化后的 provider 由 ``get_result`` 消费。
        """

        self._snapshot_loader = snapshot_loader
        self._snapshot: ActiveTabSnapshot | None = None

    def _load_snapshot_once(self) -> ActiveTabSnapshot:
        """在 provider 生命周期内至多执行一次浏览器快照读取。

        功能：首次调用时执行注入的 loader 并缓存不可变快照；后续
        URL、HTML 等子指标复用同一对象，确保多指标合取具有一致时点。
        输入参数：无。
        输出返回值：当前 provider 唯一的 ``ActiveTabSnapshot``。
        """

        if self._snapshot is None:
            self._snapshot = self._snapshot_loader()
        return self._snapshot

    def get_result(
        self,
        result_config: Dict[str, Any],
    ) -> Tuple[Any, str]:
        """按 OSWorld result 配置投影一次活动页快照。

        功能：支持原始 ``active_tab_url_parse`` 和
        ``active_tab_html_parse`` schema。URL 查询参数使用
        ``urllib.parse.parse_qs`` 解码；HTML schema 根据已确认选中的
        筛选标签生成平面布尔字典。
        输入参数：
            result_config: 含 ``type`` 与 ``parse_keys`` 的 result 配置。
        输出返回值：
            ``(投影字典, result type)`` 二元组。
        异常：
            result type 不受支持时抛出 ``ValueError``。
        """

        result_type = str(result_config.get("type") or "")
        snapshot = self._load_snapshot_once()
        if result_type == "active_tab_url_parse":
            parse_keys = [
                str(key) for key in result_config.get("parse_keys", [])
            ]
            if snapshot.page_kind != "google_shopping":
                return {key: "" for key in parse_keys}, result_type
            query = parse_qs(
                urlsplit(snapshot.url).query,
                keep_blank_values=True,
            )
            projected = {}
            for key in parse_keys:
                values = query.get(str(key), [])
                # URL parse key 是单值契约。重复值既不能只取首项假通过，
                # 也不属于评价器故障；投影为空让该 Agent 状态正常失败。
                projected[str(key)] = (
                    values[0] if len(values) == 1 else ""
                )
            replace = result_config.get("replace")
            if replace is not None:
                if not isinstance(replace, dict):
                    raise ActiveTabEvaluatorError(
                        "active_tab_url_parse replace 必须是字典"
                    )
                for source_key, target_key in replace.items():
                    source_key = str(source_key)
                    if source_key not in projected:
                        raise ActiveTabEvaluatorError(
                            "active_tab_url_parse replace 引用了未提取键: "
                            f"{source_key}"
                        )
                    projected[str(target_key)] = projected.pop(source_key)
            return projected, result_type

        if result_type == "active_tab_html_parse":
            projected = _project_selected_filters(snapshot, result_config)
            return projected, result_type

        raise ValueError(f"不支持的 active-tab result type: {result_type}")
