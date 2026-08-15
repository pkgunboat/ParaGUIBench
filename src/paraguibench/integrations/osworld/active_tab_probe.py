"""Google Shopping OSWorld active-tab 的 CDP/accessibility 证据探针。

本模块只负责从一台 VM 采集一个不可变 observation；是否通过
由 ``evaluation.osworld.state`` 的纯函数协议判定。生产路径用地址栏在
CDP 采样前后各读一次，防止把不同时刻的 URL 与 DOM 筛选状态拼接。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import ipaddress
import json
import logging
import re
from typing import Any, ClassVar
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit
from urllib.request import OpenerDirector, ProxyHandler, build_opener
import xml.etree.ElementTree as ElementTree

from paraguibench.integrations.osworld.state_contracts import (
    GoogleShoppingActiveTabObservation,
)


class OSWorldActiveTabProbeError(RuntimeError):
    """表示活动页身份、DOM 完整性或浏览器 I/O 无法可靠确定。"""


_PAGE_OBSERVATION_SCRIPT = r"""
() => {
  const cleanText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const isRendered = (element) => {
    if (!(element instanceof Element)) return false;
    if (element.closest('[aria-hidden="true"]')) return false;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const selectedLabels = new Set();
  const filterRootSelector = [
    '[aria-label*="filter" i]',
    '[data-filter]',
    '#filters',
    '[role="navigation"][aria-label*="shop" i]',
    '.fT28tf'
  ].join(',');
  const filterRoots = Array.from(document.querySelectorAll(filterRootSelector))
    .filter(isRendered);
  const inFilterSurface = (element) => Boolean(element.closest(filterRootSelector));
  const resetActionPattern = /^(?:clear all(?: filters?)?|clear filters?|reset(?: all)?(?: filters?)?|remove all filters?)$/i;
  const individualRemovePrefix = /^(?:remove(?:\s+filter)?|clear\s+filter)[\s:–—-]+/i;
  const actionText = (element) => cleanText(
    element.getAttribute('aria-label')
    || element.getAttribute('title')
    || element.innerText
    || element.textContent
  );
  const isResetAction = (element) => resetActionPattern.test(actionText(element));
  const candidateLabel = (element) => {
    const actionSelector = [
      '[aria-label^="Remove " i]',
      '[title^="Remove " i]',
      '[aria-label^="Clear filter" i]',
      '[title^="Clear filter" i]'
    ].join(',');
    const actionElement = element.matches(actionSelector)
      ? element
      : element.querySelector(actionSelector);
    const raw = (
      element.getAttribute('data-filter-label')
      || (actionElement && (
        actionElement.getAttribute('aria-label')
        || actionElement.getAttribute('title')
      ))
      || element.getAttribute('aria-label')
      || element.getAttribute('title')
      || (
        element instanceof HTMLInputElement
        && element.labels
        && element.labels.length
        && element.labels[0].innerText
      )
      || element.innerText
      || element.textContent
      || ""
    );
    return cleanText(raw).replace(individualRemovePrefix, "").trim();
  };

  const selectedSelector = [
    'input:checked',
    '[aria-checked="true"]',
    '[aria-selected="true"]',
    '[aria-pressed="true"]',
    '[data-selected="true"]',
    '[aria-label^="Remove " i]',
    '[title^="Remove " i]'
  ].join(',');
  for (const element of document.querySelectorAll(selectedSelector)) {
    if (!isRendered(element)) continue;
    if (!inFilterSurface(element) || isResetAction(element)) continue;
    const label = candidateLabel(element);
    if (label) selectedLabels.add(label);
  }
  for (const element of document.querySelectorAll('.fT28tf')) {
    if (!isRendered(element) || isResetAction(element)) continue;
    const label = candidateLabel(element);
    if (label) selectedLabels.add(label);
  }

  // 2026-07 Google Shopping 用唯一 All filters 控件键定完整状态列表。
  // 脚本仅返回原始结构；Python 层验证唯一根、全部直属子项和状态语法。
  const googleFilterStateLists = [];
  for (const root of document.querySelectorAll('div[role="list"]')) {
    if (!isRendered(root)) continue;
    const directChildren = Array.from(root.children).filter(isRendered);
    const allFiltersControls = directChildren.filter((element) => (
      element.matches('[role="button"]')
      && ["All filters", "All filters."].includes(actionText(element))
    ));
    if (allFiltersControls.length === 0) continue;
    const items = directChildren.filter(
      (element) => element.matches('[role="listitem"]')
    );
    const knownChildren = new Set([...allFiltersControls, ...items]);
    googleFilterStateLists.push({
      allFiltersControlCount: allFiltersControls.length,
      unknownRenderedChildCount: directChildren.filter(
        (element) => !knownChildren.has(element)
      ).length,
      items: items.map((item) => ({
        filterControlNames: Array.from(item.querySelectorAll('a[aria-label]'))
          .filter(isRendered)
          .map((element) => actionText(element))
      }))
    });
  }

  // 通用降级路径只在唯一 Selected filters 语义列表完整时声明闭集。
  const selectedRootSelector = [
    '[role="list"][aria-label="Selected filters" i]',
    '[role="list"][aria-label*="selected filters" i]',
    '[role="list"][data-selected-filters="true"]'
  ].join(',');
  const selectedRoots = Array.from(
    document.querySelectorAll(selectedRootSelector)
  ).filter(isRendered);
  let selectionEnumerationComplete = false;
  let selectionEvidence = "";
  if (selectedRoots.length === 1) {
    const selectedRoot = selectedRoots[0];
    const summaryLabels = new Set();
    let unparsedItemCount = 0;
    const selectedItems = Array.from(selectedRoot.children).filter(isRendered);
    for (const item of selectedItems) {
      if (isResetAction(item)) continue;
      if (!item.matches('[role="listitem"], [data-filter-label]')) {
        unparsedItemCount += 1;
        continue;
      }
      const label = candidateLabel(item);
      if (label) summaryLabels.add(label);
      else unparsedItemCount += 1;
    }
    const broadEvidenceIsSubset = Array.from(selectedLabels).every(
      (label) => summaryLabels.has(label)
    );
    if (unparsedItemCount === 0 && broadEvidenceIsSubset) {
      selectionEnumerationComplete = true;
      selectionEvidence = "semantic_selected_filter_list";
      for (const label of summaryLabels) selectedLabels.add(label);
    } else {
      selectionEvidence = "semantic_selected_filter_list_conflict";
    }
  } else if (selectedRoots.length > 1) {
    selectionEvidence = "multiple_selected_filter_lists";
  } else if (Array.from(document.querySelectorAll('.fT28tf')).some(isRendered)) {
    selectionEvidence = "legacy_fT28tf_unverified";
  } else if (filterRoots.length > 0 || selectedLabels.size > 0) {
    selectionEvidence = "partial_filter_surface";
  }

  const pageText = cleanText(document.body && document.body.innerText).toLowerCase();
  const currentHost = String(location.hostname || "").toLowerCase().replace(/\.$/, "");
  const captchaPath = (
    location.pathname === "/sorry"
    || location.pathname.startsWith("/sorry/")
  );
  const captchaChallengeSelector = [
    'form#captcha-form',
    'form[action*="/sorry/"]',
    '.g-recaptcha[data-sitekey]',
    'iframe[src*="/recaptcha/"]'
  ].join(',');
  const captchaChallengeObserved = Array.from(
    document.querySelectorAll(captchaChallengeSelector)
  ).some(isRendered);
  const captchaTitle = cleanText(document.title).toLowerCase();
  const captchaTitleEvidence = (
    captchaTitle.includes("unusual traffic") || /\bsorry\b/i.test(captchaTitle)
  );
  const captchaTextEvidence = (
    pageText.includes("unusual traffic from your computer network")
    || pageText.includes("our systems have detected unusual traffic")
  );
  const consentSurfaceSelector = [
    'form[action*="consent.google"]',
    'form[action*="/consent"]',
    '[role="dialog"]',
    'dialog[open]'
  ].join(',');
  const consentSurfaceObserved = Array.from(
    document.querySelectorAll(consentSurfaceSelector)
  ).some((element) => (
    isRendered(element)
    && cleanText(element.innerText || element.textContent)
      .toLowerCase()
      .includes("before you continue to google")
  ));
  const trustedGoogleHosts = new Set([
    "google.com", "www.google.com", "google.com.hk", "www.google.com.hk",
    "shopping.google.com", "consent.google.com"
  ]);
  let blockedReason = "";
  if (
    currentHost === "consent.google.com"
    || (trustedGoogleHosts.has(currentHost) && consentSurfaceObserved)
  ) {
    blockedReason = "google_consent";
  } else if (
    trustedGoogleHosts.has(currentHost)
    && (captchaPath || (
      captchaChallengeObserved && (captchaTitleEvidence || captchaTextEvidence)
    ))
  ) {
    blockedReason = "google_captcha";
  }

  return {
    url: String(location.href || ""),
    title: String(document.title || ""),
    locale: String(document.documentElement.lang || navigator.language || ""),
    focused: Boolean(document.hasFocus()),
    visibilityState: String(document.visibilityState || ""),
    filterSurfaceObserved: (
      filterRoots.length > 0 || selectedRoots.length > 0
      || googleFilterStateLists.length > 0 || selectedLabels.size > 0
    ),
    selectionEnumerationComplete,
    selectionEvidence,
    selectedFilterLabels: Array.from(selectedLabels),
    googleFilterStateLists,
    captchaChallengeObserved,
    consentSurfaceObserved,
    blockedReason
  };
}
"""


_GOOGLE_SELECTED_FILTER_NAME = re.compile(
    r"^Remove (?P<label>.+?) filter\. Selected\.$",
    re.IGNORECASE,
)
_GOOGLE_UNSELECTED_FILTER_NAME = re.compile(
    r"^(?P<label>.+?) filter\. Not selected\.$",
    re.IGNORECASE,
)
_GOOGLE_LEGACY_SELECTED_FILTER_NAME = re.compile(
    r"^(?:Remove )?(?P<label>.+?) filter\. Selected\.$",
    re.IGNORECASE,
)
_TRUSTED_GOOGLE_HOSTS = frozenset(
    {
        "google.com",
        "www.google.com",
        "google.com.hk",
        "www.google.com.hk",
        "shopping.google.com",
        "consent.google.com",
    }
)
_MAX_PAGE_COUNT = 64
_MAX_LABEL_COUNT = 128
_MAX_LABEL_LENGTH = 512
_MAX_TRACE_ROOTS = 8
_MAX_TRACE_NAMES_PER_ITEM = 8
_ACCESSIBILITY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_STRING_FIELD_LIMITS = {
    "url": 16_384,
    "title": 4_096,
    "locale": 64,
    "visibility_state": 32,
    "selection_evidence": 128,
    "blocked_reason": 64,
}


@dataclass(frozen=True, slots=True)
class ActivePageObservation:
    """保存一个 CDP 页面在单次脚本执行中采集的不可变状态。

    输入参数：
        url/title/locale：页面 URL、标题和语言环境。
        focused/visibility_state：DOM 同时点的焦点和可见性。
        filter_surface_observed：是否观察到筛选表面。
        selected_filter_labels：语义控件报告的已选标签。
        selection_enumeration_complete/selection_evidence：闭集完整性
            与对应 adapter 证据标识。
        captcha_challenge_observed/consent_surface_observed/blocked_reason：
            强阻塞 DOM 信号与脚本结论。
    输出返回值：
        不可变页面 observation；由 capture 在地址栏对齐后转换为
        ``GoogleShoppingActiveTabObservation``。
    """

    url: str
    title: str
    locale: str
    focused: bool
    visibility_state: str
    filter_surface_observed: bool
    selected_filter_labels: tuple[str, ...]
    selection_enumeration_complete: bool
    selection_evidence: str
    captcha_challenge_observed: bool
    consent_surface_observed: bool
    blocked_reason: str

    _STRING_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("url", "url"),
        ("title", "title"),
        ("locale", "locale"),
        ("visibilityState", "visibility_state"),
        ("selectionEvidence", "selection_evidence"),
        ("blockedReason", "blocked_reason"),
    )
    _BOOLEAN_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("focused", "focused"),
        ("filterSurfaceObserved", "filter_surface_observed"),
        ("selectionEnumerationComplete", "selection_enumeration_complete"),
        ("captchaChallengeObserved", "captcha_challenge_observed"),
        ("consentSurfaceObserved", "consent_surface_observed"),
    )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ActivePageObservation:
        """严格校验 JavaScript payload 并转换为不可变值。

        输入参数：
            payload：``_PAGE_OBSERVATION_SCRIPT`` 返回的映射。
        输出返回值：
            类型、容量与 Google 闭集 trace 均已校验的 observation。
        异常：
            OSWorldActiveTabProbeError：字段缺失、类型漂移或容量超限。
        """

        if not isinstance(payload, Mapping):
            raise OSWorldActiveTabProbeError("active-tab payload 不是对象")
        strings: dict[str, str] = {}
        booleans: dict[str, bool] = {}
        for wire_name, attribute_name in cls._STRING_FIELDS:
            value = payload.get(wire_name)
            if (
                not isinstance(value, str)
                or len(value) > _STRING_FIELD_LIMITS[attribute_name]
            ):
                raise OSWorldActiveTabProbeError(
                    f"active-tab payload 字段类型无效: {wire_name}"
                )
            strings[attribute_name] = value
        for wire_name, attribute_name in cls._BOOLEAN_FIELDS:
            value = payload.get(wire_name)
            if type(value) is not bool:
                raise OSWorldActiveTabProbeError(
                    f"active-tab payload 字段类型无效: {wire_name}"
                )
            booleans[attribute_name] = value

        raw_labels = payload.get("selectedFilterLabels")
        if not isinstance(raw_labels, (list, tuple)):
            raise OSWorldActiveTabProbeError(
                "active-tab payload selectedFilterLabels 不是列表"
            )
        if len(raw_labels) > _MAX_LABEL_COUNT:
            raise OSWorldActiveTabProbeError("active-tab payload 筛选标签超限")
        if not all(
            isinstance(label, str)
            and bool(label.strip())
            and len(label) <= _MAX_LABEL_LENGTH
            for label in raw_labels
        ):
            raise OSWorldActiveTabProbeError("active-tab payload 筛选标签无效")
        selected_labels = tuple(raw_labels)
        if len(set(selected_labels)) != len(selected_labels):
            raise OSWorldActiveTabProbeError("active-tab payload 筛选标签重复")
        if strings["visibility_state"] not in {"visible", "hidden"}:
            raise OSWorldActiveTabProbeError(
                "active-tab payload visibilityState 无法解释"
            )
        derived = _derive_google_filter_state_list(payload, selected_labels)
        if derived is not None:
            complete, evidence, selected_labels = derived
            booleans["selection_enumeration_complete"] = complete
            strings["selection_evidence"] = evidence

        return cls(
            selected_filter_labels=selected_labels,
            **strings,
            **booleans,
        )


def _clean_filter_trace_text(value: Any) -> str:
    """压缩 accessible name 中的布局空白但不修改语义。

    输入参数：
        value：Google filter-state trace 中的待校验值。
    输出返回值：
        首尾去空且连续空白折叠为单空格的文本。
    """

    return " ".join(str(value).split())


def _normalize_legacy_selected_filter_evidence(
    raw_labels: Sequence[str],
) -> set[str]:
    """有限净化通用 selector 的已选标签，用于闭集覆盖校验。

    输入参数：
        raw_labels：宽泛 DOM selector 收集的原始标签。
    输出返回值：
        需被 Google 完整状态列表覆盖的非空标签集。
    """

    normalized: set[str] = set()
    for raw_label in raw_labels:
        label = _clean_filter_trace_text(raw_label)
        if not label or label in {"All filters", "All filters."}:
            continue
        selected_match = _GOOGLE_LEGACY_SELECTED_FILTER_NAME.fullmatch(label)
        if selected_match is not None:
            label = _clean_filter_trace_text(selected_match.group("label"))
        if label:
            normalized.add(label)
    return normalized


def _derive_google_filter_state_list(
    payload: Mapping[str, Any],
    fallback_labels: tuple[str, ...],
) -> tuple[bool, str, tuple[str, ...]] | None:
    """从 Google filter-state list 原始 trace 严格推导闭集已选项。

    输入参数：
        payload：页面脚本的完整 payload。
        fallback_labels：通用 selected selector 观察到的标签。
    输出返回值：
        没有 Google 候选根时返回 ``None``；否则返回
        ``(是否完整, 证据标识, 已选或诊断标签)``。
    异常：
        OSWorldActiveTabProbeError：trace 顶层类型或容量超出固定边界。
    """

    raw_roots = payload.get("googleFilterStateLists")
    if not isinstance(raw_roots, (list, tuple)):
        raise OSWorldActiveTabProbeError(
            "active-tab payload googleFilterStateLists 不是列表"
        )
    if len(raw_roots) > _MAX_TRACE_ROOTS:
        raise OSWorldActiveTabProbeError("Google filter-state 根数量超限")
    if not raw_roots:
        return None
    if len(raw_roots) != 1:
        return False, "multiple_google_filter_state_lists", fallback_labels

    conflict = (
        False,
        "semantic_google_filter_state_list_conflict",
        fallback_labels,
    )
    root = raw_roots[0]
    if not isinstance(root, Mapping):
        return conflict
    sentinel_count = root.get("allFiltersControlCount")
    unknown_child_count = root.get("unknownRenderedChildCount")
    if (
        type(sentinel_count) is not int
        or type(unknown_child_count) is not int
        or sentinel_count != 1
        or unknown_child_count != 0
    ):
        return conflict

    raw_items = root.get("items")
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        return conflict
    if len(raw_items) > _MAX_LABEL_COUNT:
        raise OSWorldActiveTabProbeError("Google filter-state 项目数量超限")

    observed_states: dict[str, bool] = {}
    selected_labels: list[str] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            return conflict
        raw_names = raw_item.get("filterControlNames")
        if not isinstance(raw_names, (list, tuple)):
            return conflict
        if len(raw_names) > _MAX_TRACE_NAMES_PER_ITEM:
            raise OSWorldActiveTabProbeError("Google filter-state 单项名称数量超限")
        if len(raw_names) != 1 or not isinstance(raw_names[0], str):
            return conflict
        name = _clean_filter_trace_text(raw_names[0])
        if not name or len(name) > _MAX_LABEL_LENGTH:
            raise OSWorldActiveTabProbeError("Google filter-state 名称无效")
        selected_match = _GOOGLE_SELECTED_FILTER_NAME.fullmatch(name)
        unselected_match = _GOOGLE_UNSELECTED_FILTER_NAME.fullmatch(name)
        if selected_match is not None:
            label = _clean_filter_trace_text(selected_match.group("label"))
            is_selected = True
        elif unselected_match is not None:
            label = _clean_filter_trace_text(unselected_match.group("label"))
            is_selected = False
        else:
            return conflict
        if not label or label in observed_states:
            return conflict
        observed_states[label] = is_selected
        if is_selected:
            selected_labels.append(label)

    broad_evidence = _normalize_legacy_selected_filter_evidence(fallback_labels)
    if not broad_evidence.issubset(set(selected_labels)):
        return conflict
    return (
        True,
        "semantic_google_filter_state_list",
        tuple(selected_labels),
    )


def _is_google_shopping_url(url: str) -> bool:
    """判断 URL 是否属于允许的 HTTPS Google Shopping 页形态。

    输入参数：
        url：CDP 页面的完整 URL。
    输出返回值：
        精确受信主机且符合 ``tbm=shop``、``udm=28``、
        ``/shopping`` 或 shopping 专用主机时返回真。
    """

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        return False
    if host not in _TRUSTED_GOOGLE_HOSTS - {"consent.google.com"}:
        return False
    if host == "shopping.google.com":
        return True
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        query.get("tbm", [""])[0] == "shop"
        or query.get("udm", [""])[0] == "28"
        or parsed.path.rstrip("/").endswith("/shopping")
    )


def _normalized_url_identity(url: str) -> tuple[Any, ...]:
    """将 CDP URL 与 Chrome 地址栏的表示差异收敛为匹配键。

    输入参数：
        url：完整 URL 或可能省略 scheme/``www`` 的地址栏文本。
    输出返回值：
        scheme、主机、端口、路径、排序 query 与 fragment 组成的键。
    异常：
        OSWorldActiveTabProbeError：文本为空或 HTTP(S) URL 缺少主机。
    """

    text = str(url or "").strip()
    if not text:
        raise OSWorldActiveTabProbeError("Chrome 地址栏 URL 为空")
    parsed = urlsplit(text)
    if not parsed.scheme:
        parsed = urlsplit("https://" + text)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme in {"http", "https"} and not host:
        raise OSWorldActiveTabProbeError("Chrome 地址栏 URL 缺少主机")
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"
    try:
        port = parsed.port
    except ValueError as error:
        raise OSWorldActiveTabProbeError("Chrome 地址栏 URL 端口无效") from error
    return (
        scheme,
        host,
        port,
        path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
        unquote(parsed.fragment),
    )


def select_active_page_observation(
    observations: Sequence[ActivePageObservation],
    *,
    active_url_hint: str = "",
) -> ActivePageObservation:
    """从同一 CDP 采样集合中选出唯一活动标签页。

    输入参数：
        observations：同一浏览器连接的所有完整页面快照。
        active_url_hint：可选 Chrome UI 地址栏 URL；生产 capture 始终提供。
    输出返回值：
        地址栏唯一匹配页，或无 hint 时的唯一 focused/visible 页。
    异常：
        OSWorldActiveTabProbeError：页面不存在、匹配不唯一或活动页歧义。
    """

    if not observations:
        raise OSWorldActiveTabProbeError("CDP 没有可观察页面")
    if active_url_hint:
        hint_identity = _normalized_url_identity(active_url_hint)
        matches = [
            observation
            for observation in observations
            if _normalized_url_identity(observation.url) == hint_identity
        ]
        if len(matches) == 1:
            return matches[0]
        raise OSWorldActiveTabProbeError("Chrome 地址栏与 CDP 页面无法唯一配对")

    focused = [observation for observation in observations if observation.focused]
    if len(focused) == 1:
        return focused[0]
    if len(focused) > 1:
        raise OSWorldActiveTabProbeError("CDP 活动标签页存在 focus 歧义")
    visible = [
        observation
        for observation in observations
        if observation.visibility_state.lower() == "visible"
    ]
    if len(visible) == 1:
        return visible[0]
    raise OSWorldActiveTabProbeError("CDP 活动标签页存在 visibility 歧义")


def _extract_active_url_from_accessibility_tree(raw_tree: str) -> str:
    """从 OSWorld accessibility XML 中提取唯一可见 Chrome 地址栏。

    输入参数：
        raw_tree：guest ``/accessibility`` 返回的 AT-SPI XML。
    输出返回值：
        ``Address and search bar`` entry 的非空文本。
    异常：
        OSWorldActiveTabProbeError：XML 无法解析，或可见地址栏不是唯一。
    """

    if not isinstance(raw_tree, str) or not raw_tree.strip():
        raise OSWorldActiveTabProbeError("Chrome accessibility tree 为空")
    try:
        root = ElementTree.fromstring(raw_tree)
    except ElementTree.ParseError as error:
        raise OSWorldActiveTabProbeError(
            "Chrome accessibility tree 无法解析"
        ) from error

    candidates: list[str] = []
    for element in root.iter():
        if (
            str(element.tag).rsplit("}", 1)[-1] != "entry"
            or element.attrib.get("name") != "Address and search bar"
        ):
            continue
        states = {
            str(key).rsplit("}", 1)[-1]: str(value).lower()
            for key, value in element.attrib.items()
        }
        if states.get("showing") == "false" or states.get("visible") == "false":
            continue
        text = "".join(element.itertext()).strip()
        if text and text not in candidates:
            candidates.append(text)
    if len(candidates) != 1:
        raise OSWorldActiveTabProbeError("Chrome accessibility 地址栏无法唯一判定")
    return candidates[0]


def _validate_port(port: int, *, label: str) -> int:
    """验证宿主映射端口在 TCP 有效范围内。

    输入参数：
        port：待校验整数端口。
        label：异常中使用的非敏感配置名。
    输出返回值：
        校验后的原整数。
    异常：
        OSWorldActiveTabProbeError：布尔、非整数或越界值。
    """

    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise OSWorldActiveTabProbeError(f"{label} 无效")
    return port


def _format_endpoint(host: str, port: int) -> str:
    """校验宿主名并构造无凭据的 HTTP endpoint。

    输入参数：
        host：Docker 端口所在宿主的 IP 或 DNS 名。
        port：已通过 ``_validate_port`` 的端口。
    输出返回值：
        形如 ``http://127.0.0.1:59222`` 的 endpoint。
    异常：
        OSWorldActiveTabProbeError：主机含 scheme、路径、凭据或非法字符。
    """

    if not isinstance(host, str) or not host or host != host.strip():
        raise OSWorldActiveTabProbeError("active-tab host 无效")
    if any(character in host for character in "/?#@[]"):
        raise OSWorldActiveTabProbeError("active-tab host 无效")
    formatted_host = host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"[A-Za-z0-9.-]+", host) is None:
            raise OSWorldActiveTabProbeError("active-tab host 无效") from None
    else:
        if address.version == 6:
            formatted_host = f"[{host}]"
    return f"http://{formatted_host}:{port}"


def _read_active_url_from_accessibility_endpoint(
    host: str,
    server_port: int,
) -> str:
    """通过与 CDP 同容器的 OSWorld API 读取 Chrome 地址栏 URL。

    输入参数：
        host：OSWorld agent server 的宿主。
        server_port：与 Chromium 端口成对的 agent-server 映射端口。
    输出返回值：
        经 XML 契约检查的唯一可见地址栏文本。
    异常：
        OSWorldActiveTabProbeError：HTTP/JSON/XML 无效或响应超过 2 MiB。
    """

    endpoint = _format_endpoint(host, _validate_port(server_port, label="server_port"))
    opener = _build_proxy_free_url_opener()
    try:
        with opener.open(
            f"{endpoint}/accessibility",
            timeout=10.0,
        ) as response:
            raw_response = response.read(_ACCESSIBILITY_RESPONSE_MAX_BYTES + 1)
    except Exception as error:
        raise OSWorldActiveTabProbeError(
            "读取 Chrome accessibility tree 失败"
        ) from error
    if len(raw_response) > _ACCESSIBILITY_RESPONSE_MAX_BYTES:
        raise OSWorldActiveTabProbeError("Chrome accessibility tree 响应超限")
    try:
        payload = json.loads(raw_response.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OSWorldActiveTabProbeError(
            "Chrome accessibility tree 响应无法解析"
        ) from error
    raw_tree = payload.get("AT") if isinstance(payload, dict) else None
    if not isinstance(raw_tree, str):
        raise OSWorldActiveTabProbeError("Chrome accessibility tree 响应字段无效")
    return _extract_active_url_from_accessibility_tree(raw_tree)


def _build_proxy_free_url_opener() -> OpenerDirector:
    """构造不继承宿主代理变量的 loopback HTTP opener。

    输入参数：
        无；该 opener 只供 OSWorld 本地端口映射使用。
    输出返回值：
        显式安装空 ``ProxyHandler`` 的标准库 opener；即使宿主设置
        ``HTTP_PROXY``/``HTTPS_PROXY``，请求也不会离开 loopback 路径。
    """

    return build_opener(ProxyHandler({}))


def _read_page_payloads_with_playwright(
    endpoint: str,
) -> Sequence[Mapping[str, Any]]:
    """通过 lazy-import Playwright 读取当前 Chrome 的所有普通页面。

    输入参数：
        endpoint：当前容器映射的 HTTP CDP endpoint。
    输出返回值：
        每个存活 page 在各自单次 JavaScript 执行中生成的 payload。
    异常：
        OSWorldActiveTabProbeError：Playwright 缺失、CDP 不可达、页面过多，
            或任一仍存活页面读取失败。
    """

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise OSWorldActiveTabProbeError(
            "active-tab probe 需要 live extra 中的 Playwright"
        ) from error

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(
            endpoint,
            timeout=10_000,
        )
        pages = [page for context in browser.contexts for page in context.pages]
        if not pages:
            raise OSWorldActiveTabProbeError("CDP 连接成功但没有可观察页面")
        if len(pages) > _MAX_PAGE_COUNT:
            raise OSWorldActiveTabProbeError("CDP 页面数量超限")

        payloads: list[Mapping[str, Any]] = []
        read_error_count = 0
        for page in pages:
            if page.is_closed():
                continue
            try:
                payload = page.evaluate(_PAGE_OBSERVATION_SCRIPT)
                if isinstance(payload, Mapping):
                    payloads.append(payload)
                else:
                    read_error_count += 1
            except Exception:
                # 枚举后已关闭的页面不可能再成为活动页；其它失败
                # 可能遮蔽前台页，不得基于剩余页集合继续评分。
                if not page.is_closed():
                    read_error_count += 1
        if read_error_count:
            raise OSWorldActiveTabProbeError("CDP 页面观察不完整，拒绝基于残缺集合评分")
        if not payloads:
            raise OSWorldActiveTabProbeError("CDP 页面在读取前均已关闭")
        return payloads
    except OSWorldActiveTabProbeError:
        raise
    except Exception as error:
        raise OSWorldActiveTabProbeError("active-tab CDP 连接或读取失败") from error
    finally:
        # 只停止 Playwright 客户端，不调用 browser.close，避免关闭 Agent Chrome。
        playwright.stop()


def _trusted_block_reason(observation: ActivePageObservation) -> str:
    """在 Python 主机边界内复核 consent/CAPTCHA 强阻塞信号。

    输入参数：
        observation：已通过 payload schema 校验的活动页。
    输出返回值：
        ``google_consent``、``google_captcha`` 或空字符串。
    异常：
        OSWorldActiveTabProbeError：JavaScript 返回了未知阻塞标识。
    """

    if observation.blocked_reason not in {
        "",
        "google_consent",
        "google_captcha",
    }:
        raise OSWorldActiveTabProbeError("active-tab payload 阻塞标识无效")
    parsed = urlsplit(observation.url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _TRUSTED_GOOGLE_HOSTS:
        return ""
    if host == "consent.google.com" or observation.consent_surface_observed:
        return "google_consent"
    path = parsed.path
    if (
        path == "/sorry"
        or path.startswith("/sorry/")
        or observation.captcha_challenge_observed
    ):
        return "google_captcha"
    # 普通 Google 页的正文可能引用帮助文案；没有路径或可见表面
    # 时只是弱信号，不能把本可评分的 Agent 状态升级为基础设施错误。
    return ""


def _to_state_observation(
    observation: ActivePageObservation,
) -> GoogleShoppingActiveTabObservation:
    """把唯一活动页转换为纯评价层的固定 observation。

    输入参数：
        observation：AT→CDP→AT 一致性校验后的唯一页面快照。
    输出返回值：
        URL q、locale 和 DOM 筛选闭集仍来自同一页面对象的
        ``GoogleShoppingActiveTabObservation``。
    异常：
        OSWorldActiveTabProbeError：受信 Google 页被阻塞，或 Shopping 页
            locale 缺失/非英文，无法可靠解释筛选 accessible name。
    """

    blocked_reason = _trusted_block_reason(observation)
    if blocked_reason:
        raise OSWorldActiveTabProbeError(
            f"Google 活动页被阻塞，无法可靠评价: {blocked_reason}"
        )
    if _is_google_shopping_url(observation.url):
        locale = observation.locale.lower().replace("_", "-")
        if re.fullmatch(r"en(?:-[a-z0-9]{1,8})*", locale) is None:
            raise OSWorldActiveTabProbeError("Google Shopping locale 缺失或不受支持")
    return GoogleShoppingActiveTabObservation(
        url=observation.url,
        locale=observation.locale,
        filter_surface_observed=observation.filter_surface_observed,
        selection_enumeration_complete=(observation.selection_enumeration_complete),
        selection_evidence=observation.selection_evidence,
        selected_filter_labels=observation.selected_filter_labels,
        # 受信强阻塞已在上方抛 evaluator error；第三方或仅文案弱信号
        # 不得穿透到纯评价层并冒充 Google 基础设施错误。
        blocked_reason="",
    )


def capture_google_shopping_active_tab_observation(
    host: str,
    chromium_port: int,
    server_port: int,
    log: logging.Logger | None = None,
    *,
    page_payload_loader: (Callable[[str], Sequence[Mapping[str, Any]]] | None) = None,
    active_url_loader: Callable[[str, int], str] | None = None,
) -> GoogleShoppingActiveTabObservation:
    """从一台 OSWorld VM 捕获 Google Shopping 活动页不可变快照。

    输入参数：
        host：Docker 宿主 IP/DNS；通常为 loopback。
        chromium_port：当前容器 ``9222`` 的宿主动态映射端口。
        server_port：同一容器 agent server 的宿主映射端口；用于
            两次读取 Chrome UI 地址栏。
        log：可选日志器；只记录页面类型和页数，不记录 URL/标签。
        page_payload_loader：可注入的 CDP loader；省略时 lazy-import Playwright。
        active_url_loader：可注入的地址栏 loader；省略时调用
            OSWorld ``/accessibility`` endpoint。
    输出返回值：
        可直接交给 ``OSWorldChromeStateEvidenceSource`` 的单 VM observation。
    异常：
        OSWorldActiveTabProbeError：端口、I/O、schema、活动页身份、采样竞态、
            语言或阻塞状态无法可靠确定。
    """

    chromium_port = _validate_port(chromium_port, label="chromium_port")
    server_port = _validate_port(server_port, label="server_port")
    if chromium_port == server_port:
        raise OSWorldActiveTabProbeError("chromium_port 与 server_port 不得重复")
    endpoint = _format_endpoint(host, chromium_port)
    address_loader = active_url_loader or _read_active_url_from_accessibility_endpoint
    payload_loader = page_payload_loader or _read_page_payloads_with_playwright

    try:
        active_url_before = address_loader(host, server_port)
    except OSWorldActiveTabProbeError:
        raise
    except Exception as error:
        raise OSWorldActiveTabProbeError("Chrome 地址栏首次采样失败") from error
    try:
        payloads = payload_loader(endpoint)
    except OSWorldActiveTabProbeError:
        raise
    except Exception as error:
        raise OSWorldActiveTabProbeError("active-tab CDP 页面采样失败") from error
    if isinstance(payloads, (str, bytes)) or not isinstance(payloads, Sequence):
        raise OSWorldActiveTabProbeError("active-tab CDP payload 集合类型无效")
    if not payloads or len(payloads) > _MAX_PAGE_COUNT:
        raise OSWorldActiveTabProbeError("active-tab CDP payload 集合为空或超限")
    observations = [ActivePageObservation.from_payload(payload) for payload in payloads]
    try:
        active_url_after = address_loader(host, server_port)
    except OSWorldActiveTabProbeError:
        raise
    except Exception as error:
        raise OSWorldActiveTabProbeError("Chrome 地址栏二次采样失败") from error
    if _normalized_url_identity(active_url_before) != _normalized_url_identity(
        active_url_after
    ):
        raise OSWorldActiveTabProbeError("AT→CDP→AT 采样期间活动标签页发生变化")
    selected = select_active_page_observation(
        observations,
        active_url_hint=active_url_after,
    )
    result = _to_state_observation(selected)
    if log is not None:
        log.info(
            "OSWorld active-tab 快照已冻结: page_kind=%s page_count=%d",
            "google_shopping" if _is_google_shopping_url(selected.url) else "other",
            len(observations),
        )
    return result


__all__ = [
    "ActivePageObservation",
    "OSWorldActiveTabProbeError",
    "capture_google_shopping_active_tab_observation",
    "select_active_page_observation",
]
