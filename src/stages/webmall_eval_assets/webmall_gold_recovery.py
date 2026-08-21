"""WebMall 可恢复任务的可观测商品规则与动态 gold 构建工具。"""

from __future__ import annotations

import html
import hashlib
import json
import re
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_XBOX_SERIES_S_RE = re.compile(r"\bxbox\s+series\s+s\b", re.IGNORECASE)
_WHITE_RE = re.compile(r"\b(?:white|hvit)\b", re.IGNORECASE)
_STORAGE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(tb|gb)\b", re.IGNORECASE)
_EXCLUDED_CONDITION_RE = re.compile(
    r"\b(?:used|pre[\s-]?owned|refurbished|renewed)\b",
    re.IGNORECASE,
)
_REFRESH_RATE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*hz\b", re.IGNORECASE)
_RESPONSE_TIME_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*ms\b", re.IGNORECASE)

XBOX_TASK_ID = "Operation-OnlineShopping-CheapestOfferSpecificRequirements-004"
MONITOR_TASK_ID = "Operation-OnlineShopping-CheapestOfferVagueRequirements-006"
RECOVERY_SNAPSHOT_ID = "webmall-easy-recovery-20260721"

FetchPage = Callable[
    [str],
    Tuple[Sequence[Mapping[str, Any]], Mapping[str, str]],
]


def _plain_product_text(product: Mapping[str, Any]) -> str:
    """拼接并规范化商品页中允许用于判定的可见文本。

    功能：读取 WooCommerce Store API 的商品名、短描述和正文，解码 HTML
    实体、移除标签并统一空白，使规则不会依赖具体 HTML 排版。
    输入参数：product 为单条 Store API 商品映射。
    输出返回值：可用于正则判定的单行可见文本。
    """

    raw_text = " ".join(
        str(product.get(field) or "")
        for field in ("name", "short_description", "description")
    )
    without_tags = _HTML_TAG_RE.sub(" ", html.unescape(raw_text))
    return " ".join(without_tags.replace("‑", "-").split())


def _maximum_storage_gb(text: str) -> float:
    """提取页面明示存储容量并统一换算为 GB。

    功能：扫描文本中的 TB/GB 数值，按 1 TB=1024 GB 换算后返回最大值；
    不把显存单位 MB 或未标单位数字误认为磁盘容量。
    输入参数：text 为已规范化的商品可见文本。
    输出返回值：页面中最大的 GB 容量；未找到明确容量时返回 0。
    """

    capacities = []
    for raw_value, raw_unit in _STORAGE_RE.findall(text):
        value = float(raw_value)
        capacities.append(value * 1024 if raw_unit.lower() == "tb" else value)
    return max(capacities, default=0.0)


def classify_xbox_offer(product: Mapping[str, Any]) -> Dict[str, Any]:
    """按修订后的 Xbox 任务契约分类一条商品 offer。

    功能：仅接受在库且可购、明确属于 Xbox Series S、页面明示白色和至少
    512 GB 容量，并且未被标记为二手或翻新的商品；返回逐项排除原因，供
    gold 证据表和回归测试共同使用。
    输入参数：product 为 WooCommerce Store API 单条商品映射。
    输出返回值：包含 qualifies、reasons、evidence_text 和 storage_gb 的字典。
    """

    text = _plain_product_text(product)
    reasons = []
    if product.get("is_in_stock") is not True:
        reasons.append("not_in_stock")
    if product.get("is_purchasable") is not True:
        reasons.append("not_purchasable")
    if not _XBOX_SERIES_S_RE.search(text):
        reasons.append("wrong_product_family")
    if not _WHITE_RE.search(text):
        reasons.append("wrong_color")
    storage_gb = _maximum_storage_gb(text)
    if storage_gb < 512:
        reasons.append("insufficient_or_missing_storage")
    if _EXCLUDED_CONDITION_RE.search(text):
        reasons.append("excluded_condition")

    return {
        "qualifies": not reasons,
        "reasons": reasons,
        "evidence_text": text,
        "storage_gb": storage_gb,
    }


def classify_monitor_offer(product: Mapping[str, Any]) -> Dict[str, Any]:
    """按修订后的竞技显示器任务契约分类一条商品 offer。

    功能：要求商品明确属于 gaming monitor，页面明示刷新率至少 180 Hz、
    响应时间至多 1 ms，并满足在库和可购条件；缺失数值与不达阈值使用
    不同原因，避免把未披露规格误当作合格。
    输入参数：product 为 WooCommerce Store API 单条商品映射。
    输出返回值：包含 qualifies、reasons、evidence_text、refresh_rate_hz
    和 response_time_ms 的分类证据字典。
    """

    text = _plain_product_text(product)
    lowered_text = text.lower()
    refresh_rates = [float(value) for value in _REFRESH_RATE_RE.findall(text)]
    response_times = [float(value) for value in _RESPONSE_TIME_RE.findall(text)]
    refresh_rate_hz = max(refresh_rates, default=None)
    response_time_ms = min(response_times, default=None)

    reasons = []
    if product.get("is_in_stock") is not True:
        reasons.append("not_in_stock")
    if product.get("is_purchasable") is not True:
        reasons.append("not_purchasable")
    if "gaming" not in lowered_text or "monitor" not in lowered_text:
        reasons.append("not_gaming_monitor")
    if refresh_rate_hz is None:
        reasons.append("missing_refresh_rate")
    elif refresh_rate_hz < 180:
        reasons.append("refresh_below_minimum")
    if response_time_ms is None:
        reasons.append("missing_response_time")
    elif response_time_ms > 1:
        reasons.append("response_above_maximum")

    return {
        "qualifies": not reasons,
        "reasons": reasons,
        "evidence_text": text,
        "refresh_rate_hz": refresh_rate_hz,
        "response_time_ms": response_time_ms,
    }


def derive_cheapest_offers(
    products: Iterable[Mapping[str, Any]],
    classifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Dict[str, Any]:
    """从完整商品目录推导满足规则的最低价 URL 集合。

    功能：对每条商品调用公开分类器，保留全部合格 offer，并以 Store API
    的最小货币单位价格选择所有并列最低价 URL；同时返回逐商品分类证据，
    便于人工复核没有漏掉更便宜候选。
    输入参数：products 为 Store API 商品序列；classifier 为返回 qualifies
    和 reasons 等证据的任务规则函数。
    输出返回值：包含最低价、严格 expected_urls、全部合格 offer 和完整
    evaluated_offers 证据表的字典；没有合格商品时最低价为 None、URL 为空。
    """

    evaluated_offers = []
    qualifying_offers = []
    for product in products:
        classification = dict(classifier(product))
        price_raw = (product.get("prices") or {}).get("price")
        try:
            price_minor = int(str(price_raw))
        except (TypeError, ValueError):
            price_minor = 0
            classification["qualifies"] = False
            classification.setdefault("reasons", []).append("invalid_price")

        offer = {
            "id": product.get("id"),
            "url": str(product.get("permalink") or ""),
            "price_minor": price_minor,
            **classification,
        }
        if not offer["url"]:
            offer["qualifies"] = False
            offer.setdefault("reasons", []).append("missing_permalink")
        evaluated_offers.append(offer)
        if offer["qualifies"]:
            qualifying_offers.append(offer)

    minimum_price = min(
        (offer["price_minor"] for offer in qualifying_offers),
        default=None,
    )
    expected_urls = sorted(
        offer["url"]
        for offer in qualifying_offers
        if offer["price_minor"] == minimum_price
    )
    return {
        "minimum_price_minor": minimum_price,
        "expected_urls": expected_urls,
        "qualifying_offers": qualifying_offers,
        "evaluated_offers": evaluated_offers,
    }


def _classification_summary(evaluated_offers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """汇总一项任务对完整目录的分类覆盖情况。

    功能：统计已评价、合格和各排除原因的商品数量，使快照能够证明规则
    确实遍历了全目录，而不是只保存最终 gold 附近的少数候选。
    输入参数：evaluated_offers 为 derive_cheapest_offers 返回的逐商品证据。
    输出返回值：包含 evaluated、qualified 和 excluded_by_reason 的汇总字典。
    """

    excluded_by_reason: Dict[str, int] = {}
    qualified = 0
    for offer in evaluated_offers:
        if offer.get("qualifies") is True:
            qualified += 1
            continue
        for reason in offer.get("reasons") or []:
            excluded_by_reason[str(reason)] = excluded_by_reason.get(str(reason), 0) + 1
    return {
        "evaluated": len(evaluated_offers),
        "qualified": qualified,
        "excluded_by_reason": dict(sorted(excluded_by_reason.items())),
    }


def _compact_decision(
    rule_version: str,
    derived: Mapping[str, Any],
) -> Dict[str, Any]:
    """把完整推导结果压缩为可审阅、可版本化的任务决策。

    功能：保留最低价、全部并列 URL、合格商品证据和全目录分类计数，省略
    数千条逐商品正文，控制仓库快照体积但不丢失 gold 决策依据。
    输入参数：rule_version 为可追溯规则版本；derived 为最低价推导结果。
    输出返回值：可写入恢复快照 decisions 的任务决策字典。
    """

    qualifying_offers = sorted(
        (
            {
                key: offer.get(key)
                for key in (
                    "id",
                    "url",
                    "price_minor",
                    "storage_gb",
                    "refresh_rate_hz",
                    "response_time_ms",
                    "evidence_text",
                )
                if key in offer
            }
            for offer in derived["qualifying_offers"]
        ),
        key=lambda offer: str(offer.get("url") or ""),
    )
    return {
        "action": "restore_eval",
        "rule_version": rule_version,
        "minimum_price_minor": derived["minimum_price_minor"],
        "expected_urls": list(derived["expected_urls"]),
        "qualifying_offers": qualifying_offers,
        "classification_summary": _classification_summary(
            derived["evaluated_offers"]
        ),
    }


def build_recovery_snapshot(
    catalog_by_store: Mapping[str, Sequence[Mapping[str, Any]]],
    store_metadata: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> Dict[str, Any]:
    """从完整四店目录构建 Xbox 与显示器恢复快照。

    功能：校验每店声明总量与实际分页结果一致，计算规范化全目录 SHA-256，
    再分别应用两个公开规则生成严格最低价集合；任一任务没有合格商品时
    立即失败，禁止生成空 gold 后误取消隔离。
    输入参数：catalog_by_store 为端口到完整商品序列的映射；store_metadata
    为端口到 total、total_pages、last_modified 的映射；generated_at 为带
    时区的 ISO 时间字符串。
    输出返回值：包含目录版本、摘要和两个任务决策的可序列化快照字典。
    """

    normalized_catalog: Dict[str, list[Mapping[str, Any]]] = {}
    for port in sorted(catalog_by_store):
        products = list(catalog_by_store[port])
        declared_total = int(store_metadata.get(port, {}).get("total", -1))
        if declared_total != len(products):
            raise ValueError(
                f"store {port} catalog is incomplete: "
                f"declared={declared_total}, fetched={len(products)}"
            )
        normalized_catalog[port] = sorted(
            products,
            key=lambda product: (
                int(product.get("id") or 0),
                str(product.get("permalink") or ""),
            ),
        )

    canonical_payload = json.dumps(
        {
            "stores": store_metadata,
            "catalog": normalized_catalog,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    all_products = [
        product
        for port in sorted(normalized_catalog)
        for product in normalized_catalog[port]
    ]
    xbox = derive_cheapest_offers(all_products, classify_xbox_offer)
    monitor = derive_cheapest_offers(all_products, classify_monitor_offer)
    if not xbox["expected_urls"]:
        raise ValueError("Xbox recovery rule produced an empty gold set")
    if not monitor["expected_urls"]:
        raise ValueError("monitor recovery rule produced an empty gold set")

    return {
        "snapshot_id": RECOVERY_SNAPSHOT_ID,
        "generated_at": generated_at,
        "catalog_total": len(all_products),
        "catalog_sha256": hashlib.sha256(canonical_payload).hexdigest(),
        "stores": {port: dict(store_metadata[port]) for port in sorted(store_metadata)},
        "decisions": {
            XBOX_TASK_ID: _compact_decision("xbox-series-s-v1", xbox),
            MONITOR_TASK_ID: _compact_decision("competitive-monitor-v1", monitor),
        },
    }


def fetch_store_catalog(
    base_url: str,
    *,
    fetch_page: FetchPage,
    per_page: int = 100,
) -> Tuple[list[Mapping[str, Any]], Dict[str, Any]]:
    """读取一家 WooCommerce 商店的完整 Store API 商品目录。

    功能：先读取第一页响应头确定总页数和声明总量，再逐页请求并验证实际
    商品数；HTTP 获取函数通过参数注入，使网络边界可测试且生产实现只能
    执行明确的 GET 页面读取。
    输入参数：base_url 为商店 origin；fetch_page 为接收 URL 并返回商品
    序列及响应头的系统边界函数；per_page 为每页商品数。
    输出返回值：完整商品列表以及 total、total_pages、last_modified 元数据。
    """

    if per_page <= 0:
        raise ValueError("per_page must be positive")
    endpoint = base_url.rstrip("/") + "/wp-json/wc/store/v1/products"
    first_url = f"{endpoint}?per_page={per_page}&page=1"
    first_products, first_headers = fetch_page(first_url)
    try:
        total = int(first_headers["X-WP-Total"])
        total_pages = int(first_headers["X-WP-TotalPages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Store API response is missing catalog pagination headers") from exc

    products = list(first_products)
    for page in range(2, total_pages + 1):
        page_url = f"{endpoint}?per_page={per_page}&page={page}"
        page_products, _ = fetch_page(page_url)
        products.extend(page_products)
    if len(products) != total:
        raise ValueError(
            f"Store API catalog is incomplete: declared={total}, fetched={len(products)}"
        )

    return products, {
        "total": total,
        "total_pages": total_pages,
        "last_modified": str(first_headers.get("Last-Modified") or ""),
    }


def verify_recovery_snapshot(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> Dict[str, Any]:
    """比较已审阅恢复快照与现场重建结果是否可安全评分。

    功能：忽略每次执行必然变化的 generated_at，只比较目录 SHA-256、四店
    分页/版本元数据以及每项任务的规则版本、最低价和严格 URL 集合；任何
    差异都会形成稳定字段路径，供运行前闸门停止评分并提示重新审阅。
    输入参数：expected 为仓库内已审阅快照；observed 为现场全目录重建快照。
    输出返回值：包含 matches 布尔值和排序后的 mismatches 字段路径列表。
    """

    mismatches = []
    if expected.get("snapshot_id") != observed.get("snapshot_id"):
        mismatches.append("snapshot_id")
    if expected.get("catalog_sha256") != observed.get("catalog_sha256"):
        mismatches.append("catalog_sha256")

    expected_stores = expected.get("stores") or {}
    observed_stores = observed.get("stores") or {}
    if set(expected_stores) != set(observed_stores):
        mismatches.append("stores")
    for port in sorted(set(expected_stores) & set(observed_stores)):
        for field in ("total", "total_pages", "last_modified"):
            if expected_stores[port].get(field) != observed_stores[port].get(field):
                mismatches.append(f"stores.{port}.{field}")

    expected_decisions = expected.get("decisions") or {}
    observed_decisions = observed.get("decisions") or {}
    if set(expected_decisions) != set(observed_decisions):
        mismatches.append("decisions")
    for task_id in sorted(set(expected_decisions) & set(observed_decisions)):
        for field in (
            "rule_version",
            "minimum_price_minor",
            "expected_urls",
        ):
            if expected_decisions[task_id].get(field) != observed_decisions[task_id].get(
                field
            ):
                mismatches.append(f"decisions.{task_id}.{field}")

    return {
        "matches": not mismatches,
        "mismatches": sorted(mismatches),
    }
