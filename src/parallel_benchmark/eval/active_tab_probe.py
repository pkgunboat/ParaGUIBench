"""通过浏览器语义状态确定活动标签页的 I/O 探针。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Callable, Mapping, Sequence, Tuple
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit
import xml.etree.ElementTree as ElementTree

from parallel_benchmark.eval.active_tab_evaluator import (
    ActiveTabEvaluatorError,
    ActiveTabSnapshot,
)


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
    const individualActionSelector = [
      '[aria-label^="Remove " i]',
      '[title^="Remove " i]',
      '[aria-label^="Clear filter" i]',
      '[title^="Clear filter" i]'
    ].join(',');
    const actionElement = (
      element.matches(individualActionSelector)
      ? element
      : element.querySelector(individualActionSelector)
    );
    const raw = (
      element.getAttribute('data-filter-label')
      || (
        actionElement
        && (
          actionElement.getAttribute('aria-label')
          || actionElement.getAttribute('title')
        )
      )
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

  // 兼容原任务使用的旧版已选 filter chip；该 class 只是降级信号，
  // 主路径仍依赖 checked/ARIA/removable-chip 等语义状态。
  for (const element of document.querySelectorAll('.fT28tf')) {
    if (!isRendered(element) || isResetAction(element)) continue;
    const label = candidateLabel(element);
    if (label) selectedLabels.add(label);
  }

  // 2026-07 真实 Google Shopping 不再暴露 “Selected filters” 列表，
  // 而是在一个由直属 “All filters.” 控件锚定的 filter-state list 中，
  // 通过每个链接的 accessible name 同时表达标签与选中状态。这里只
  // 采集原始、可审计的结构，不在 JavaScript 中静默猜测完整性；Python
  // 契约层会检查唯一根、全部直属子项及每个 item 的全部 aria-label。
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
        filterControlNames: Array.from(
          item.querySelectorAll('a[aria-label]')
        )
          .filter(isRendered)
          .map((element) => actionText(element))
      }))
    });
  }

  // “未发现额外筛选”是闭集断言，不能由宽泛 filter root 或任意一个
  // checked 控件推出。仅当页面提供唯一、显式标注的 selected-filter
  // 语义列表，且其中所有 listitem 均可解析时，才允许 complete=true。
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
    const selectedItems = Array.from(selectedRoot.children)
      .filter(isRendered);
    for (const item of selectedItems) {
      if (isResetAction(item)) continue;
      if (!item.matches('[role="listitem"], [data-filter-label]')) {
        unparsedItemCount += 1;
        continue;
      }
      const label = candidateLabel(item);
      if (label) {
        summaryLabels.add(label);
      } else {
        unparsedItemCount += 1;
      }
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
  } else if (
    Array.from(document.querySelectorAll('.fT28tf')).some(isRendered)
  ) {
    selectionEvidence = "legacy_fT28tf_unverified";
  } else if (filterRoots.length > 0 || selectedLabels.size > 0) {
    selectionEvidence = "partial_filter_surface";
  }

  const pageText = cleanText(document.body && document.body.innerText).toLowerCase();
  const currentUrl = String(location.href || "");
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
    captchaTitle.includes("unusual traffic")
    || /\bsorry\b/i.test(captchaTitle)
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
    "google.com",
    "www.google.com",
    "google.com.hk",
    "www.google.com.hk",
    "shopping.google.com",
    "consent.google.com"
  ]);
  let blockedReason = "";
  if (
    currentHost === "consent.google.com"
    || (
      trustedGoogleHosts.has(currentHost)
      && consentSurfaceObserved
    )
  ) {
    blockedReason = "google_consent";
  } else if (
    trustedGoogleHosts.has(currentHost)
    && (
      captchaPath
      || (
        captchaChallengeObserved
        && (captchaTitleEvidence || captchaTextEvidence)
      )
    )
  ) {
    blockedReason = "google_captcha";
  }

  return {
    url: currentUrl,
    title: String(document.title || ""),
    locale: String(
      document.documentElement.lang
      || navigator.language
      || ""
    ),
    focused: Boolean(document.hasFocus()),
    visibilityState: String(document.visibilityState || ""),
    filterSurfaceObserved: (
      filterRoots.length > 0
      || selectedRoots.length > 0
      || googleFilterStateLists.length > 0
      || selectedLabels.size > 0
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


@dataclass(frozen=True)
class ActivePageObservation:
    """保存单个 CDP 页面在同一次采样中的可见状态。

    功能：把 Playwright 页面对象转换为可离线测试的不可变值对象。
    输入参数：
        url: 页面当前完整 URL。
        title: 页面标题。
        locale: ``document.documentElement.lang`` 或浏览器语言。
        focused: ``document.hasFocus()`` 的采样结果。
        visibility_state: ``document.visibilityState``。
        filter_surface_observed: 是否观察到可解释的筛选控件表面。
        selected_filter_labels: 通过语义状态确认已选中的筛选标签。
        selection_enumeration_complete: 是否有闭集证据证明选中项枚举完整。
        selection_evidence: 完整性或降级结论对应的 adapter 证据标识。
        captcha_challenge_observed: 是否观察到可见 CAPTCHA 表单或 iframe；
            它只与受信主机及阻塞文案组合使用，正文文字本身不是充分证据。
        consent_surface_observed: 是否观察到包含明确 consent 文案的可见
            form/dialog；普通正文引用该文案时保持为弱信号。
        blocked_reason: consent、captcha 等不可评分页面阻塞原因。
    输出返回值：
        不可变的 ``ActivePageObservation`` 实例。
    """

    url: str
    title: str
    locale: str
    focused: bool
    visibility_state: str
    filter_surface_observed: bool
    selected_filter_labels: Tuple[str, ...]
    selection_enumeration_complete: bool = False
    selection_evidence: str = ""
    captcha_challenge_observed: bool = False
    consent_surface_observed: bool = False
    blocked_reason: str = ""


def _is_google_shopping_url(url: str) -> bool:
    """判断 URL 是否属于 Google Shopping 页面。

    功能：针对本任务的英文/USD 环境，兼容 ``shopping.google.com``
    首页、旧 ``tbm=shop`` 搜索和新 ``udm=28`` 搜索；仅信任
    google.com、www.google.com、现场观测到的 google.com.hk 精确主机
    与 shopping.google.com；避免第三方或用户可控 Google 子域仅凭
    查询参数伪装为 Shopping。
    输入参数：
        url: 待分类的完整页面 URL。
    输出返回值：
        属于受支持 Google Shopping 页面时返回 ``True``。
    """

    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    trusted_hosts = {
        "google.com",
        "www.google.com",
        "google.com.hk",
        "www.google.com.hk",
        "shopping.google.com",
    }
    if hostname not in trusted_hosts:
        return False
    if hostname == "shopping.google.com":
        return True
    query = parse_qs(parsed.query)
    return (
        query.get("tbm", [""])[0] == "shop"
        or query.get("udm", [""])[0] == "28"
        or parsed.path.rstrip("/").endswith("/shopping")
    )


def _is_trusted_google_block_url(url: str) -> bool:
    """判断阻塞信号是否来自本 adapter 信任的 Google 主机。

    功能：为 JavaScript DOM 信号增加独立 Python 主机边界；只有
    Shopping 搜索主机或明确的 ``consent.google.com`` 才能把 consent/
    captcha 标记升级为评价器错误，第三方同路径或引用文本保持普通
    wrong-site 失败。
    输入参数：
        url: 活动页面完整 URL。
    输出返回值：
        URL 主机可承载 Google 阻塞信号时返回 ``True``。
    """

    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return hostname in {
        "google.com",
        "www.google.com",
        "google.com.hk",
        "www.google.com.hk",
        "shopping.google.com",
        "consent.google.com",
    }


def _is_google_captcha_path(url: str) -> bool:
    """判断 URL 是否位于 Google 明确的 ``/sorry`` CAPTCHA 路径。

    功能：
        仅匹配路径本身等于 ``/sorry`` 或以 ``/sorry/`` 开头的页面；
        不检查 query 中嵌入的 ``continue=https://.../sorry/...``，避免
        把普通搜索页中的参数文字当成活动页路径。
    输入参数：
        url: 活动标签页的完整 URL。
    输出返回值：
        页面自身路径属于 Google CAPTCHA 路径时返回 ``True``。
    """

    path = urlsplit(str(url or "")).path
    return path == "/sorry" or path.startswith("/sorry/")


def _is_google_consent_host(url: str) -> bool:
    """判断 URL 是否属于 Google 明确的 consent 专用主机。

    功能：
        对活动页 URL 做主机解析，只接受精确 ``consent.google.com``；
        不通过后缀、子串或页面正文扩大信任边界。
    输入参数：
        url: 活动标签页的完整 URL。
    输出返回值：
        主机精确等于 Google consent 主机时返回 ``True``。
    """

    hostname = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    return hostname == "consent.google.com"


def build_active_tab_snapshot(
    observation: ActivePageObservation,
) -> ActiveTabSnapshot:
    """把唯一活动页面观察值转换为评价层快照。

    功能：依据经过主机边界校验的 URL 标记 Google Shopping，其它
    活动页面标记为 ``other``；保留 locale、筛选表面与已选标签证据。
    输入参数：
        observation: ``select_active_page_observation`` 选出的唯一页面。
    输出返回值：
        可供 ``ActiveTabResultProvider`` 共享的不可变快照。
    """

    blocked_reason = observation.blocked_reason
    if (
        blocked_reason == "google_captcha"
        and not (
            _is_google_captcha_path(observation.url)
            or observation.captcha_challenge_observed
        )
    ):
        # 页面正文可能引用 Google 的 unusual-traffic 帮助文案。路径和
        # 可见挑战表面均不存在时，该文本只能视为弱信号，不能把一个
        # 原本可评价的 Agent 状态升级为基础设施错误。
        blocked_reason = ""
    if (
        blocked_reason == "google_consent"
        and not (
            _is_google_consent_host(observation.url)
            or observation.consent_surface_observed
        )
    ):
        # 普通 Google 页面正文可能引用 consent 帮助文案。只有专用主机
        # 或可见 consent form/dialog 才能把它升级为基础设施错误。
        blocked_reason = ""

    if blocked_reason and _is_trusted_google_block_url(observation.url):
        raise ActiveTabEvaluatorError(
            f"活动页被阻塞，无法可靠评价: {blocked_reason}"
        )

    page_kind = (
        "google_shopping"
        if _is_google_shopping_url(observation.url)
        else "other"
    )
    return ActiveTabSnapshot(
        url=observation.url,
        page_kind=page_kind,
        locale=observation.locale,
        filter_surface_observed=observation.filter_surface_observed,
        selection_enumeration_complete=(
            observation.selection_enumeration_complete
        ),
        selection_evidence=observation.selection_evidence,
        selected_filter_labels=observation.selected_filter_labels,
    )


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
_GOOGLE_FILTER_TRACE_MAX_ROOTS = 8
_GOOGLE_FILTER_TRACE_MAX_ITEMS = 128
_GOOGLE_FILTER_TRACE_MAX_NAMES = 8
_GOOGLE_FILTER_TRACE_MAX_LABEL_LENGTH = 512


def _clean_filter_trace_text(value: Any) -> str:
    """压缩 Google filter trace 中的纯布局空白。

    功能：把 DOM accessible name 或旧通用探针标签转换为单空格文本；
    不折叠大小写、不删除标点，也不做模糊语义匹配。
    输入参数：
        value: 页面 payload 中待清洗的任意标量。
    输出返回值：
        去除首尾空白并压缩连续空白后的字符串。
    """

    return " ".join(str(value).split())


def _normalize_legacy_selected_filter_evidence(
    raw_labels: Sequence[str],
) -> set[str]:
    """有限净化旧通用探针产生的已选筛选证据。

    功能：忽略 filter panel 自身的 ``All filters`` 开关，把现场旧探针
    产生的 ``<label> filter. Selected.`` 后缀恢复为标签；其它非空
    标签保持原义。净化结果只用于检查新版闭集是否覆盖所有宽泛证据，
    不作为最终选中集合。
    输入参数：
        raw_labels: 通用 selected selector 收集的原始标签序列。
    输出返回值：
        需要由新版 filter-state list 覆盖的标签集合。
    """

    normalized = set()
    for raw_label in raw_labels:
        label = _clean_filter_trace_text(raw_label)
        if not label or label in {"All filters", "All filters."}:
            continue
        selected_match = _GOOGLE_LEGACY_SELECTED_FILTER_NAME.fullmatch(label)
        if selected_match is not None:
            label = _clean_filter_trace_text(
                selected_match.group("label")
            )
        if label:
            normalized.add(label)
    return normalized


def _derive_google_filter_state_list(
    payload: Mapping[str, Any],
    fallback_labels: Tuple[str, ...],
) -> Tuple[bool, str, Tuple[str, ...]] | None:
    """从新版 Google filter-state list trace 推导闭集选中项。

    功能：严格校验页面脚本采集的原始结构。只有唯一候选根、唯一
    ``All filters`` 哨兵、零未知直属子项、每个 listitem 唯一状态链接、
    状态语法完整且标签无重复时才返回 ``complete=True``。任何结构
    歧义均保留旧标签用于诊断，并把完整性保持为假。
    输入参数：
        payload: 页面脚本返回的完整结构化对象。
        fallback_labels: 通用 selected selector 已收集的标签。
    输出返回值：
        未观察到新版候选根时返回 ``None``；否则返回
        ``(完整性, 证据标识, 最终或诊断标签)``。
    异常：
        trace 顶层类型或容量违反固定 I/O 契约时抛出
        ``ActiveTabEvaluatorError``。
    """

    if "googleFilterStateLists" not in payload:
        return None
    raw_roots = payload.get("googleFilterStateLists")
    if not isinstance(raw_roots, (list, tuple)):
        raise ActiveTabEvaluatorError(
            "CDP 页面观察结果的 googleFilterStateLists 不是列表"
        )
    if len(raw_roots) > _GOOGLE_FILTER_TRACE_MAX_ROOTS:
        raise ActiveTabEvaluatorError(
            "CDP 页面观察结果的 Google filter-state 根数量超限"
        )
    if not raw_roots:
        return None
    if len(raw_roots) > 1:
        return (
            False,
            "multiple_google_filter_state_lists",
            fallback_labels,
        )

    root = raw_roots[0]
    conflict = (
        False,
        "semantic_google_filter_state_list_conflict",
        fallback_labels,
    )
    if not isinstance(root, Mapping):
        return conflict
    all_filters_count = root.get("allFiltersControlCount")
    unknown_child_count = root.get("unknownRenderedChildCount")
    if (
        type(all_filters_count) is not int
        or type(unknown_child_count) is not int
        or all_filters_count != 1
        or unknown_child_count != 0
    ):
        return conflict

    raw_items = root.get("items")
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        return conflict
    if len(raw_items) > _GOOGLE_FILTER_TRACE_MAX_ITEMS:
        raise ActiveTabEvaluatorError(
            "CDP 页面观察结果的 Google filter-state 项目数量超限"
        )

    selected_labels = []
    observed_states: dict[str, bool] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            return conflict
        raw_names = raw_item.get("filterControlNames")
        if not isinstance(raw_names, (list, tuple)):
            return conflict
        if len(raw_names) > _GOOGLE_FILTER_TRACE_MAX_NAMES:
            raise ActiveTabEvaluatorError(
                "CDP 页面观察结果的单个 filter-state 名称数量超限"
            )
        if len(raw_names) != 1 or not isinstance(raw_names[0], str):
            return conflict
        name = _clean_filter_trace_text(raw_names[0])
        if len(name) > _GOOGLE_FILTER_TRACE_MAX_LABEL_LENGTH:
            raise ActiveTabEvaluatorError(
                "CDP 页面观察结果的 filter-state 名称长度超限"
            )
        selected_match = _GOOGLE_SELECTED_FILTER_NAME.fullmatch(name)
        unselected_match = _GOOGLE_UNSELECTED_FILTER_NAME.fullmatch(name)
        if selected_match is not None:
            label = _clean_filter_trace_text(
                selected_match.group("label")
            )
            is_selected = True
        elif unselected_match is not None:
            label = _clean_filter_trace_text(
                unselected_match.group("label")
            )
            is_selected = False
        else:
            return conflict
        if not label or label in observed_states:
            return conflict
        observed_states[label] = is_selected
        if is_selected:
            selected_labels.append(label)

    broad_evidence = _normalize_legacy_selected_filter_evidence(
        fallback_labels
    )
    if not broad_evidence.issubset(set(selected_labels)):
        return conflict
    return (
        True,
        "semantic_google_filter_state_list",
        tuple(selected_labels),
    )


def _observation_from_payload(
    payload: Mapping[str, Any],
) -> ActivePageObservation:
    """校验页面脚本 payload 并转换为不可变观察值。

    功能：集中处理 JavaScript 与 Python 的字段边界，拒绝非对象 payload
    以及非列表的 selected filter 标签，避免 DOM 脚本漂移被静默转换为
    空状态。
    输入参数：
        payload: ``_PAGE_OBSERVATION_SCRIPT`` 返回的结构化对象。
    输出返回值：
        经类型收敛后的 ``ActivePageObservation``。
    异常：
        payload 契约不合法时抛出 ``ActiveTabEvaluatorError``。
    """

    if not isinstance(payload, Mapping):
        raise ActiveTabEvaluatorError("CDP 页面观察结果不是对象")
    raw_labels = payload.get("selectedFilterLabels", [])
    if not isinstance(raw_labels, (list, tuple)):
        raise ActiveTabEvaluatorError(
            "CDP 页面观察结果的 selectedFilterLabels 不是列表"
        )
    selected_filter_labels = tuple(
        str(label) for label in raw_labels if str(label).strip()
    )
    derived_google_state = _derive_google_filter_state_list(
        payload,
        selected_filter_labels,
    )
    if derived_google_state is None:
        selection_enumeration_complete = bool(
            payload.get("selectionEnumerationComplete")
        )
        selection_evidence = str(payload.get("selectionEvidence") or "")
    else:
        (
            selection_enumeration_complete,
            selection_evidence,
            selected_filter_labels,
        ) = derived_google_state
    return ActivePageObservation(
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
        locale=str(payload.get("locale") or ""),
        focused=bool(payload.get("focused")),
        visibility_state=str(payload.get("visibilityState") or ""),
        filter_surface_observed=bool(payload.get("filterSurfaceObserved")),
        selected_filter_labels=selected_filter_labels,
        selection_enumeration_complete=selection_enumeration_complete,
        selection_evidence=selection_evidence,
        captcha_challenge_observed=bool(
            payload.get("captchaChallengeObserved")
        ),
        consent_surface_observed=bool(
            payload.get("consentSurfaceObserved")
        ),
        blocked_reason=str(payload.get("blockedReason") or ""),
    )


def _normalized_url_identity(url: str) -> Tuple[Any, ...]:
    """把页面 URL 或 Chrome 地址栏文本规范化为匹配键。

    功能：地址栏常省略 scheme 与 ``www``，查询参数也可能以不同顺序
    展示；本函数只消除这些表示差异，不按域名或页面类型赋予优先级。
    输入参数：
        url: CDP 完整 URL 或 accessibility 地址栏文本。
    输出返回值：
        ``(scheme, host, port, path, query, fragment)`` 不可变匹配键。
    异常：
        空文本或无法解析出主机的 HTTP(S) 地址时抛出
        ``ActiveTabEvaluatorError``。
    """

    text = str(url or "").strip()
    if not text:
        raise ActiveTabEvaluatorError("Chrome 地址栏 URL 为空")
    parsed = urlsplit(text)
    if not parsed.scheme:
        parsed = urlsplit("https://" + text)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme in {"http", "https"} and not host:
        raise ActiveTabEvaluatorError(
            f"Chrome 地址栏 URL 缺少主机: {url!r}"
        )
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"
    query = tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        scheme,
        host,
        parsed.port,
        path,
        query,
        unquote(parsed.fragment),
    )


def _read_active_url_from_accessibility_tree(
    vm_ip: str,
    server_port: int,
) -> str:
    """从 guest Chrome UI 地址栏读取活动标签页 URL。

    功能：通过与 CDP 端口同 VM 的 ``PythonController`` 获取桌面
    accessibility tree，只接受唯一、可见且非空的
    ``Address and search bar`` entry。该证据来自浏览器 UI，避免同一
    Chrome 窗口的多个页面同时错误报告 DOM focus/visibility 时误判。
    输入参数：
        vm_ip: VM API 所在宿主地址。
        server_port: 当前容器映射到宿主机的 Python Server 端口。
    输出返回值：
        Chrome 地址栏原始文本；scheme 或 ``www`` 可由后续匹配层补齐。
    异常：
        端口无效、树不可读/不可解析或地址栏不唯一时抛出
        ``ActiveTabEvaluatorError``。
    """

    if not isinstance(server_port, int) or not 1 <= server_port <= 65535:
        raise ActiveTabEvaluatorError(
            f"无效 server_port: {server_port!r}"
        )
    try:
        from desktop_env.controllers.python import PythonController

        controller = PythonController(
            vm_ip=vm_ip,
            server_port=server_port,
        )
        raw_tree = controller.get_accessibility_tree()
    except Exception as exc:
        raise ActiveTabEvaluatorError(
            f"读取 Chrome accessibility tree 失败: {exc}"
        ) from exc
    if not raw_tree:
        raise ActiveTabEvaluatorError(
            "Chrome accessibility tree 为空"
        )
    try:
        root = ElementTree.fromstring(raw_tree)
    except Exception as exc:
        raise ActiveTabEvaluatorError(
            f"解析 Chrome accessibility tree 失败: {exc}"
        ) from exc

    candidates = []
    for element in root.iter():
        local_tag = str(element.tag).rsplit("}", 1)[-1]
        if (
            local_tag != "entry"
            or element.attrib.get("name") != "Address and search bar"
        ):
            continue
        states = {
            str(key).rsplit("}", 1)[-1]: str(value).lower()
            for key, value in element.attrib.items()
        }
        if (
            states.get("showing") == "false"
            or states.get("visible") == "false"
        ):
            continue
        text = "".join(element.itertext()).strip()
        if text and text not in candidates:
            candidates.append(text)
    if len(candidates) != 1:
        raise ActiveTabEvaluatorError(
            "Chrome accessibility 地址栏不可唯一判定："
            f"有效候选数量为 {len(candidates)}"
        )
    return candidates[0]


def _read_page_payloads_with_playwright(
    endpoint: str,
) -> Sequence[Mapping[str, Any]]:
    """通过 Playwright CDP 读取当前浏览器全部普通页面。

    功能：延迟导入 Playwright，连接已由任务 Stage 1 启动的 Chrome，
    对每个现存 page 执行只读观察脚本；任一仍存活页面不可读时拒绝
    基于残缺页面集合评分。函数只停止 Playwright 客户端，不调用
    ``browser.close``，因此不会关闭 Agent 使用的浏览器。
    输入参数：
        endpoint: 当前 VM 动态映射的 HTTP CDP 地址。
    输出返回值：
        每个 page 的结构化观察 payload 序列。
    异常：
        Playwright 缺失、CDP 不可达或所有页面读取失败时抛出
        ``ActiveTabEvaluatorError``。
    """

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ActiveTabEvaluatorError(
            "active-tab evaluator 需要项目 requirements 中的 playwright"
        ) from exc

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(
            endpoint,
            timeout=10_000,
        )
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
        ]
        if not pages:
            raise ActiveTabEvaluatorError("CDP 连接成功但没有可观察页面")

        payloads = []
        read_errors = []
        for page in pages:
            if page.is_closed():
                continue
            try:
                payload = page.evaluate(_PAGE_OBSERVATION_SCRIPT)
                if isinstance(payload, Mapping):
                    payloads.append(payload)
                else:
                    read_errors.append("页面脚本返回非对象")
            except Exception as exc:
                # 页面在枚举后自然关闭时不再可能成为活动页；其它读取失败
                # 则可能恰好遮蔽前台页，必须阻止从剩余后台页继续评分。
                if not page.is_closed():
                    read_errors.append(str(exc))
        if read_errors:
            detail = "; ".join(read_errors[:3]) or "unknown"
            raise ActiveTabEvaluatorError(
                f"CDP 页面观察不完整，拒绝基于残缺页面集合评分: {detail}"
            )
        if not payloads:
            raise ActiveTabEvaluatorError(
                "所有 CDP 页面在读取前均已关闭，无法判定活动标签页"
            )
        return payloads
    except ActiveTabEvaluatorError:
        raise
    except Exception as exc:
        raise ActiveTabEvaluatorError(
            f"连接 active-tab CDP 失败: {exc}"
        ) from exc
    finally:
        playwright.stop()


def capture_active_tab_snapshot(
    vm_ip: str,
    chromium_port: int,
    log: logging.Logger | None = None,
    *,
    server_port: int | None = None,
    page_payload_loader: (
        Callable[[str], Sequence[Mapping[str, Any]]] | None
    ) = None,
    active_url_loader: Callable[[str, int], str] | None = None,
) -> ActiveTabSnapshot:
    """从当前 VM 的动态 CDP 端点抓取唯一活动页快照。

    功能：构造与 ``server_port`` 同容器配对的 CDP endpoint，读取全部
    页面观察值，先按焦点/可见性确定唯一活动页，再分类为 Google
    Shopping 或其它站点。可注入 loader 以便离线测试。
    输入参数：
        vm_ip: Docker 宿主机地址。
        chromium_port: 当前容器映射到宿主机的动态 CDP 端口。
        log: 可选日志器，用于记录所选活动页，不含页面正文。
        server_port: 与 ``chromium_port`` 来自同一容器记录的 VM API
            端口；提供 accessibility loader 时必须有效。
        page_payload_loader: 可选测试替身；接收 endpoint 并返回 payload。
        active_url_loader: 可选地址栏读取器；接收 ``vm_ip`` 与
            ``server_port`` 并返回 Chrome UI 显示的活动 URL。
    输出返回值：
        同次多指标评价共享的 ``ActiveTabSnapshot``。
    异常：
        端口无效、CDP 读取失败、活动页歧义或阻塞时抛出
        ``ActiveTabEvaluatorError``。
    """

    if not isinstance(chromium_port, int) or not 1 <= chromium_port <= 65535:
        raise ActiveTabEvaluatorError(
            f"无效 chromium_port: {chromium_port!r}"
        )
    address_loader: Callable[[str, int], str] | None = None
    active_url_before = ""
    if server_port is not None or active_url_loader is not None:
        if (
            not isinstance(server_port, int)
            or not 1 <= server_port <= 65535
        ):
            raise ActiveTabEvaluatorError(
                f"无效 server_port: {server_port!r}"
            )
        address_loader = (
            active_url_loader
            or _read_active_url_from_accessibility_tree
        )
        try:
            active_url_before = address_loader(vm_ip, server_port)
        except ActiveTabEvaluatorError:
            raise
        except Exception as exc:
            raise ActiveTabEvaluatorError(
                f"读取 Chrome 地址栏活动 URL 失败: {exc}"
            ) from exc

    endpoint = f"http://{vm_ip}:{chromium_port}"
    loader = page_payload_loader or _read_page_payloads_with_playwright
    try:
        payloads = loader(endpoint)
    except ActiveTabEvaluatorError:
        raise
    except Exception as exc:
        raise ActiveTabEvaluatorError(
            f"active-tab 页面读取失败: {exc}"
        ) from exc

    observations = [
        _observation_from_payload(payload)
        for payload in payloads
    ]
    active_url_hint = active_url_before
    if address_loader is not None:
        try:
            active_url_after = address_loader(vm_ip, server_port)
        except ActiveTabEvaluatorError:
            raise
        except Exception as exc:
            raise ActiveTabEvaluatorError(
                f"读取 Chrome 地址栏活动 URL 失败: {exc}"
            ) from exc
        if (
            _normalized_url_identity(active_url_before)
            != _normalized_url_identity(active_url_after)
        ):
            raise ActiveTabEvaluatorError(
                "AT→CDP→AT 采样期间活动标签页发生变化"
            )
        active_url_hint = active_url_after
    selected = select_active_page_observation(
        observations,
        active_url_hint=active_url_hint,
    )
    if log is not None:
        log.info(
            "active-tab 已选择: url=%s visibility=%s focused=%s",
            selected.url,
            selected.visibility_state,
            selected.focused,
        )
    return build_active_tab_snapshot(selected)


def select_active_page_observation(
    observations: Sequence[ActivePageObservation],
    *,
    active_url_hint: str = "",
) -> ActivePageObservation:
    """从同次 CDP 采样中选择唯一活动标签页。

    功能：优先使用唯一 ``document.hasFocus()`` 页面；没有焦点证据时，
    仅在恰好一个页面 ``visibilityState=visible`` 时降级选择。多个候选
    或没有候选均视为探针歧义，禁止按 URL 偏好 Shopping 页。
    输入参数：
        observations: 同一浏览器连接内全部普通页面的观察值。
        active_url_hint: 可选的 Chrome UI 地址栏 URL；提供时作为
            活动标签页的权威匹配证据。
    输出返回值：
        唯一活动页面观察值。
    异常：
        活动页不存在或不唯一时抛出 ``ActiveTabEvaluatorError``。
    """

    if active_url_hint:
        hint_identity = _normalized_url_identity(active_url_hint)
        matches = [
            item
            for item in observations
            if _normalized_url_identity(item.url) == hint_identity
        ]
        if len(matches) == 1:
            return matches[0]
        raise ActiveTabEvaluatorError(
            "Chrome 地址栏 URL 与 CDP 页面无法唯一配对："
            f"匹配数量为 {len(matches)}"
        )

    focused = [item for item in observations if item.focused]
    if len(focused) == 1:
        return focused[0]
    if len(focused) > 1:
        raise ActiveTabEvaluatorError(
            f"活动标签页歧义：同时有 {len(focused)} 个页面获得焦点"
        )

    visible = [
        item
        for item in observations
        if item.visibility_state.lower() == "visible"
    ]
    if len(visible) == 1:
        return visible[0]
    raise ActiveTabEvaluatorError(
        f"活动标签页不可判定：visible 页面数量为 {len(visible)}"
    )
