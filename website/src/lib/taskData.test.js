import test from "node:test";
import assert from "node:assert/strict";

import {
  filterTasks,
  hasActiveFilters,
  localizedValue,
  paginateTasks,
  uniqueTaskValues,
} from "./taskData.js";

const tasks = [
  {
    task_id: "InformationRetrieval-FileSearch-Readonly-001",
    benchmark_group: "FileSearch",
    evaluation_protocol: "paraguibench.answer.exact.v1",
    support_status: "live_validated",
  },
  {
    task_id: "Operation-WebOperate-Settings-001",
    benchmark_group: "WebNavigation",
    evaluation_protocol: "legacy.osworld.state.v1",
    support_status: "blocked",
  },
  {
    task_id: "Operation-OnlineShopping-Search-001",
    benchmark_group: "OnlineShopping",
    evaluation_protocol: "legacy.webmall.cart.v1",
    support_status: "blocked",
  },
];

/**
 * 验证筛选逻辑能够组合查询词和离散字段。
 */
test("filterTasks combines query and facets", () => {
  assert.deepEqual(
    filterTasks(tasks, {
      query: "settings",
      benchmarkGroup: "WebNavigation",
      evaluator: "legacy.osworld.state.v1",
      status: "blocked",
    }),
    [tasks[1]],
  );
  assert.equal(filterTasks(tasks, { benchmarkGroup: "FileSearch" }).length, 1);
});

/**
 * 验证分页会修正越界页码并保留总数。
 */
test("paginateTasks clamps page boundaries", () => {
  assert.deepEqual(paginateTasks(tasks, 8, 2), {
    items: [tasks[2]],
    page: 2,
    pageCount: 2,
    total: 3,
  });
  assert.equal(paginateTasks([], 1, 10).pageCount, 1);
});

/**
 * 验证站点只使用生成器提供的双语白名单标签。
 */
test("localizedValue returns labels and safe fallback", () => {
  const dataset = {
    labels: {
      values: {
        support_status: {
          blocked: { en: "Blocked", "zh-CN": "尚未闭环" },
        },
      },
    },
  };
  assert.equal(localizedValue(dataset, "support_status", "blocked", "zh-CN"), "尚未闭环");
  assert.equal(localizedValue(dataset, "support_status", "unknown", "en"), "unknown");
});

/**
 * 验证筛选选项去重、排空并稳定排序。
 */
test("uniqueTaskValues creates stable options", () => {
  assert.deepEqual(uniqueTaskValues(tasks, "support_status"), ["blocked", "live_validated"]);
});

/**
 * 验证重置按钮只在存在有效条件时出现。
 */
test("hasActiveFilters ignores whitespace-only query", () => {
  assert.equal(hasActiveFilters({ query: "   " }), false);
  assert.equal(hasActiveFilters({ status: "blocked" }), true);
});
