/**
 * 返回站点数据中某个枚举值的本地化标签。
 *
 * @param {object} dataset - 由 scripts/site/generate_site_data.py 生成的数据集。
 * @param {string} field - labels.values 下的字段名。
 * @param {string} value - 需要翻译的枚举原始值。
 * @param {"en" | "zh-CN"} language - 当前界面语言。
 * @returns {string} 本地化标签；缺少白名单标签时回退为原始值。
 */
export function localizedValue(dataset, field, value, language) {
  return dataset?.labels?.values?.[field]?.[value]?.[language] ?? value;
}

/**
 * 从任务列表提取稳定排序的筛选选项。
 *
 * @param {object[]} tasks - 公开任务元数据列表。
 * @param {string} field - 需要聚合的任务字段。
 * @returns {string[]} 去重且按英文字符顺序排列的字段值。
 */
export function uniqueTaskValues(tasks, field) {
  return [...new Set(tasks.map((task) => task[field]).filter(Boolean))].sort((left, right) =>
    left.localeCompare(right),
  );
}

/**
 * 按任务 ID、六类分类、评价协议和支持状态筛选任务。
 *
 * @param {object[]} tasks - 公开任务元数据列表。
 * @param {{query?: string, benchmarkGroup?: string, evaluator?: string, status?: string}} filters - 当前筛选条件。
 * @returns {object[]} 保持 canonical 顺序的筛选结果。
 */
export function filterTasks(tasks, filters) {
  const normalizedQuery = filters.query?.trim().toLocaleLowerCase() ?? "";
  return tasks.filter((task) => {
    const queryMatches =
      normalizedQuery.length === 0 || task.task_id.toLocaleLowerCase().includes(normalizedQuery);
    const groupMatches =
      !filters.benchmarkGroup || task.benchmark_group === filters.benchmarkGroup;
    const evaluatorMatches =
      !filters.evaluator || task.evaluation_protocol === filters.evaluator;
    const statusMatches = !filters.status || task.support_status === filters.status;
    return queryMatches && groupMatches && evaluatorMatches && statusMatches;
  });
}

/**
 * 对筛选后的任务执行有边界的前端分页。
 *
 * @param {object[]} tasks - 已筛选的任务列表。
 * @param {number} requestedPage - 从 1 开始的目标页码。
 * @param {number} pageSize - 每页记录数。
 * @returns {{items: object[], page: number, pageCount: number, total: number}} 当前页数据及分页信息。
 */
export function paginateTasks(tasks, requestedPage, pageSize) {
  const safePageSize = Math.max(1, Math.trunc(pageSize));
  const pageCount = Math.max(1, Math.ceil(tasks.length / safePageSize));
  const page = Math.min(Math.max(1, Math.trunc(requestedPage)), pageCount);
  const start = (page - 1) * safePageSize;
  return {
    items: tasks.slice(start, start + safePageSize),
    page,
    pageCount,
    total: tasks.length,
  };
}

/**
 * 判断筛选器是否包含任何非默认条件。
 *
 * @param {{query?: string, benchmarkGroup?: string, evaluator?: string, status?: string}} filters - 当前筛选条件。
 * @returns {boolean} 任一条件生效时返回 true。
 */
export function hasActiveFilters(filters) {
  return Boolean(
    filters.query?.trim() || filters.benchmarkGroup || filters.evaluator || filters.status,
  );
}
