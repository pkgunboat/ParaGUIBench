import React, { useEffect, useMemo, useState } from "react";

import { Icon } from "./Icon.jsx";
import {
  filterTasks,
  hasActiveFilters,
  localizedValue,
  paginateTasks,
  uniqueTaskValues,
} from "../lib/taskData.js";

const EMPTY_FILTERS = {
  query: "",
  benchmarkGroup: "",
  evaluator: "",
  status: "",
};

/**
 * 渲染任务状态标签，颜色只表达公开 manifest 中的支持状态。
 *
 * @param {{status: string, label: string}} props - 原始状态与本地化标签。
 * @returns {React.ReactElement} 带语义文本的状态标记。
 */
function StatusBadge({ status, label }) {
  return (
    <span className={`status-badge status-badge-${status}`}>
      <i aria-hidden="true" />
      {label}
    </span>
  );
}

/**
 * 渲染桌面表格和移动端堆叠布局共用的一条公开任务记录。
 *
 * @param {{task: object, dataset: object, language: "en" | "zh-CN", copy: object}} props - 任务、安全公开数据集、语言和列名文案。
 * @returns {React.ReactElement} 不包含任务正文、答案或内部路径的表格行。
 */
function TaskRow({ task, dataset, language, copy }) {
  const blockers =
    task.blocker_codes.length > 0
      ? task.blocker_codes
          .map((value) => localizedValue(dataset, "blocker_codes", value, language))
          .join(" · ")
      : copy.noBlockers;

  return (
    <tr>
      <td className="task-id-cell" data-label={copy.task} title={task.task_id}>
        {task.task_id}
      </td>
      <td data-label={copy.category}>
        {localizedValue(dataset, "benchmark_group", task.benchmark_group, language)}
      </td>
      <td data-label={copy.evaluator}>
        {localizedValue(dataset, "evaluation_protocol", task.evaluation_protocol, language)}
      </td>
      <td data-label={copy.runtime}>
        {localizedValue(dataset, "environment_protocol", task.environment_protocol, language)}
      </td>
      <td data-label={copy.assets}>
        {localizedValue(dataset, "asset_status", task.asset_status, language)}
      </td>
      <td data-label={copy.validation}>
        <div className="validation-statuses">
          <StatusBadge
            label={localizedValue(
              dataset,
              "local_readiness_status",
              task.local_readiness_status,
              language,
            )}
            status={task.local_readiness_status}
          />
          <StatusBadge
            label={localizedValue(dataset, "support_status", task.support_status, language)}
            status={task.support_status}
          />
        </div>
        <small className="blocker-text">
          <span className="visually-hidden">{copy.blockers}: </span>
          {blockers}
        </small>
      </td>
    </tr>
  );
}

/**
 * 渲染任务检索、四维筛选、公开状态统计和有边界分页。
 *
 * @param {{copy: object, dataset: object | null, language: "en" | "zh-CN", loading: boolean, error: boolean}} props - 文案、数据加载状态与当前语言。
 * @returns {React.ReactElement} 可交互任务支持浏览器。
 */
export function TaskExplorer({ copy, dataset, language, loading, error }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const tasks = dataset?.tasks ?? [];

  const groups = useMemo(() => uniqueTaskValues(tasks, "benchmark_group"), [tasks]);
  const evaluators = useMemo(() => uniqueTaskValues(tasks, "evaluation_protocol"), [tasks]);
  const statuses = useMemo(() => uniqueTaskValues(tasks, "support_status"), [tasks]);
  const filteredTasks = useMemo(() => filterTasks(tasks, filters), [tasks, filters]);
  const pagination = useMemo(() => paginateTasks(filteredTasks, page, 8), [filteredTasks, page]);

  useEffect(() => {
    setPage(1);
  }, [filters]);

  /**
   * 更新一个筛选维度，同时保留其余条件。
   *
   * @param {keyof typeof EMPTY_FILTERS} field - 需要更新的字段。
   * @param {string} value - 浏览器控件返回的筛选值。
   * @returns {void}
   */
  function updateFilter(field, value) {
    setFilters((current) => ({ ...current, [field]: value }));
  }

  /**
   * 清空全部筛选并回到第一页。
   *
   * @returns {void}
   */
  function clearFilters() {
    setFilters(EMPTY_FILTERS);
    setPage(1);
  }

  const summary = dataset?.summary;
  const localReadyCount = summary?.local_readiness_status_counts?.local_ready ?? 0;
  const liveCount = summary?.support_status_counts?.live_validated ?? 0;
  const pendingCount = summary?.support_status_counts?.blocked ?? 0;

  return (
    <section className="section section-tasks" id="tasks">
      <div className="site-shell">
        <div className="section-heading tasks-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.intro}</p>
          </div>
        </div>

        <div className="task-explorer">
          <div className="task-filter-bar">
            <label className="search-control">
              <span className="visually-hidden">{copy.search}</span>
              <Icon name="search" size={18} />
              <input
                onChange={(event) => updateFilter("query", event.target.value)}
                placeholder={copy.search}
                type="search"
                value={filters.query}
              />
            </label>
            <button
              aria-expanded={mobileFiltersOpen}
              className="mobile-filter-button"
              onClick={() => setMobileFiltersOpen((current) => !current)}
              type="button"
            >
              <Icon name="search" size={18} />
              {copy.filters}
            </button>
            <div className={`task-selects ${mobileFiltersOpen ? "task-selects-open" : ""}`}>
              <label>
                <span className="visually-hidden">{copy.allGroups}</span>
                <select
                  onChange={(event) => updateFilter("benchmarkGroup", event.target.value)}
                  value={filters.benchmarkGroup}
                >
                  <option value="">{copy.allGroups}</option>
                  {groups.map((group) => (
                    <option key={group} value={group}>
                      {localizedValue(dataset, "benchmark_group", group, language)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="visually-hidden">{copy.allEvaluators}</span>
                <select
                  onChange={(event) => updateFilter("evaluator", event.target.value)}
                  value={filters.evaluator}
                >
                  <option value="">{copy.allEvaluators}</option>
                  {evaluators.map((evaluator) => (
                    <option key={evaluator} value={evaluator}>
                      {localizedValue(dataset, "evaluation_protocol", evaluator, language)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="visually-hidden">{copy.allStatuses}</span>
                <select
                  onChange={(event) => updateFilter("status", event.target.value)}
                  value={filters.status}
                >
                  <option value="">{copy.allStatuses}</option>
                  {statuses.map((status) => (
                    <option key={status} value={status}>
                      {localizedValue(dataset, "support_status", status, language)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="clear-filter-button"
                disabled={!hasActiveFilters(filters)}
                onClick={clearFilters}
                type="button"
              >
                {copy.clear}
              </button>
            </div>
          </div>

          <div className="task-summary" aria-live="polite">
            <span>
              <strong>{summary?.task_count ?? "—"}</strong>
              {language === "zh-CN" ? "个 canonical 任务" : "canonical tasks"}
            </span>
            <span className="summary-local">
              <strong>{localReadyCount}</strong>
              {copy.localReadySummary}
            </span>
            <span className="summary-live">
              <strong>{liveCount}</strong>
              {language === "zh-CN" ? "个已真实验证" : "live validated"}
            </span>
            <span className="summary-pending">
              <strong>{pendingCount}</strong>
              {copy.blockedSummary}
            </span>
          </div>

          <div className="task-note">
            <Icon name="warning" size={17} />
            <span>{copy.note}</span>
          </div>

          {loading ? <p className="task-message">{copy.loading}</p> : null}
          {error ? <p className="task-message task-error">{copy.loadError}</p> : null}

          {!loading && !error ? (
            <>
              <div className="task-table-wrap">
                <table className="task-table">
                  <thead>
                    <tr>
                      <th>{copy.task}</th>
                      <th>{copy.category}</th>
                      <th>{copy.evaluator}</th>
                      <th>{copy.runtime}</th>
                      <th>{copy.assets}</th>
                      <th>{copy.validation}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pagination.items.map((task) => (
                      <TaskRow
                        copy={copy}
                        dataset={dataset}
                        key={task.task_id}
                        language={language}
                        task={task}
                      />
                    ))}
                  </tbody>
                </table>
                {pagination.total === 0 ? <p className="task-message">{copy.noResults}</p> : null}
              </div>

              <div className="pagination">
                <span aria-live="polite">
                  {copy.page} {pagination.page} {copy.of} {pagination.pageCount}
                </span>
                <div>
                  <button
                    disabled={pagination.page === 1}
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                    type="button"
                  >
                    <Icon name="chevron" size={16} />
                    {copy.previous}
                  </button>
                  <button
                    disabled={pagination.page === pagination.pageCount}
                    onClick={() =>
                      setPage((current) => Math.min(pagination.pageCount, current + 1))
                    }
                    type="button"
                  >
                    {copy.next}
                    <Icon className="chevron-forward" name="chevron" size={16} />
                  </button>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
