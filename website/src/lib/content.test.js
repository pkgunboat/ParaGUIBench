import assert from "node:assert/strict";
import test from "node:test";

import { content } from "../content.js";

/**
 * 将本地化对象递归转换为仅包含 key 与值类型的结构。
 *
 * @param {unknown} value - 待检查的任意本地化值。
 * @returns {unknown} 可用于深比较的稳定 schema 投影。
 */
function shape(value) {
  if (Array.isArray(value)) {
    return value.map(shape);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, shape(value[key])]),
    );
  }
  return typeof value;
}

/**
 * 把嵌套本地化内容折叠为可执行术语回归检查的文本。
 *
 * @param {unknown} value - 字符串、数组或嵌套对象。
 * @returns {string} 保留全部叶子文本的空格分隔字符串。
 */
function textOf(value) {
  if (Array.isArray(value)) {
    return value.map(textOf).join(" ");
  }
  if (value && typeof value === "object") {
    return Object.values(value).map(textOf).join(" ");
  }
  return String(value);
}

/**
 * 验证执行图与模块边界文案在中英文之间保持相同 schema。
 */
test("architecture copy keeps bilingual schema parity", () => {
  assert.deepEqual(shape(content.en.hero.diagram), shape(content["zh-CN"].hero.diagram));
  assert.deepEqual(shape(content.en.architecture), shape(content["zh-CN"].architecture));
});

/**
 * 验证当前 preview 状态不会与论文多 Worker 系统混写。
 */
test("architecture copy distinguishes historical preview evidence from the paper agent", () => {
  const en = content.en.hero.diagram;
  const zh = content["zh-CN"].hero.diagram;

  assert.equal(en.preview.scope, "1 task · 1 VM · 1 worker");
  assert.equal(zh.preview.scope, "1 个任务 · 1 台虚拟机 · 1 个 Worker");
  assert.match(en.reference.round, /Adaptive round/u);
  assert.match(zh.reference.round, /自适应轮次/u);
  assert.match(en.reference.dispatch, /Self-contained parallel dispatch/u);
  assert.match(zh.reference.dispatch, /自包含并行分派/u);
  assert.match(en.reference.packageStatus, /integration pending/iu);
  assert.match(zh.reference.packageStatus, /集成待完成/u);
  assert.match(en.preview.status, /Historical|pending/iu);
  assert.match(zh.preview.status, /历史|待复验/u);
});

/**
 * 验证 Hero 的任务状态数字来自机器数据，而不是再次手工复制到双语文案。
 */
test("hero status labels contain no hand-maintained task counts", () => {
  for (const language of ["en", "zh-CN"]) {
    const status = content[language].hero.status;
    for (const field of ["canonical", "localReady", "localIncomplete", "live", "blocked"]) {
      assert.doesNotMatch(status[field], /\d/u);
    }
  }
});

/**
 * 验证站点文案把本地组件就绪与真实环境晋升显式分层。
 */
test("readiness copy distinguishes local components from live validation", () => {
  assert.match(content.en.tasks.note, /local.*live|live.*local/iu);
  assert.match(content["zh-CN"].tasks.note, /本地.*真实环境|真实环境.*本地/u);
  assert.match(content.en.results.localReady, /Local/iu);
  assert.match(content["zh-CN"].results.localReady, /本地/u);
});

/**
 * 验证资源类型不会再次被错误提升为专用 Worker 类型。
 */
test("architecture copy never invents specialized workers", () => {
  const allCopy = textOf(content);

  assert.doesNotMatch(allCopy, /(?:Browser|Desktop) worker|(?:浏览器|桌面)\s*Worker/iu);
  assert.match(content.en.hero.diagram.reference.worker, /Generic GUI worker/u);
  assert.match(content["zh-CN"].hero.diagram.reference.worker, /通用 GUI Worker/u);
});

/**
 * 验证论文架构仍包含环境状态评价、轮次修订和禁止直连通信三项边界。
 */
test("architecture copy preserves evaluation and coordination boundaries", () => {
  const en = textOf(content.en.hero.diagram);
  const zh = textOf(content["zh-CN"].hero.diagram);

  assert.match(en, /Final environment state/u);
  assert.match(zh, /最终环境状态/u);
  assert.match(en, /not a fixed upfront DAG/u);
  assert.match(zh, /并非预先固定的 DAG/u);
  assert.match(en, /do not message one another|no direct messaging/u);
  assert.match(zh, /不直接通信/u);
});

/**
 * 验证公开源码模块图不会再把 Planner 或 Worker Runtime 归入 Framework。
 */
test("repository architecture keeps framework ownership narrow", () => {
  const frameworkEn = content.en.architecture.modules.find(
    (module) => module.name === "Framework",
  );
  const frameworkZh = content["zh-CN"].architecture.modules.find(
    (module) => module.name === "Framework",
  );

  assert.ok(frameworkEn);
  assert.ok(frameworkZh);
  assert.doesNotMatch(textOf(frameworkEn), /Task Registry|Planner|Worker Runtime/u);
  assert.doesNotMatch(textOf(frameworkZh), /任务注册表|规划器|Worker 运行时/u);
  assert.match(textOf(content.en.architecture), /Framework never creates a VM/u);
  assert.match(textOf(content["zh-CN"].architecture), /Framework 不创建虚拟机/u);
});

/**
 * 验证站点不会把 OSWorld 状态协议的代码接线误写为真实环境通过。
 */
test("OSWorld state copy distinguishes migrated code from live validation", () => {
  const en = textOf({
    benchmark: content.en.benchmark,
    architecture: content.en.architecture,
    results: content.en.results,
  });
  const zh = textOf({
    benchmark: content["zh-CN"].benchmark,
    architecture: content["zh-CN"].architecture,
    results: content["zh-CN"].results,
  });

  assert.match(en, /profile.*active-tab/iu);
  assert.match(zh, /profile.*active-tab/iu);
  assert.match(en, /15 artifact-state tasks remain blocked/iu);
  assert.match(zh, /15 个 artifact-state 任务仍处于阻塞/u);
  assert.match(en, /versioned live validation remains pending/iu);
  assert.match(zh, /版本化真实环境验证仍待执行/u);
});
