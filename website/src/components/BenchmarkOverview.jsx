import React from "react";

import { Icon } from "./Icon.jsx";
import { localizedValue } from "../lib/taskData.js";

const groupIcons = {
  WebSearch: "search",
  FileSearch: "folder",
  OnlineShopping: "browser",
  FileOperation: "desktop",
  WebNavigation: "browser",
  SearchAndWrite: "code",
};

const canonicalGroupOrder = [
  "WebSearch",
  "FileSearch",
  "OnlineShopping",
  "FileOperation",
  "WebNavigation",
  "SearchAndWrite",
];

/**
 * 绘制串行与并行执行的紧凑对照轨道。
 *
 * @param {{mode: "sequential" | "parallel", title: string, notes: string[]}} props - 轨道模式、标题与解释要点。
 * @returns {React.ReactElement} 适用于桌面和移动端的比较图。
 */
function ExecutionComparison({ mode, title, notes }) {
  const parallel = mode === "parallel";
  return (
    <article className={`comparison-panel comparison-${mode}`}>
      <h3>{title}</h3>
      <div className="comparison-visual" aria-hidden="true">
        <span className="mini-node">
          <Icon name="planner" size={18} />
        </span>
        <div className="mini-tracks">
          {Array.from({ length: parallel ? 3 : 1 }, (_, index) => (
            <div className="mini-track" key={index}>
              <i />
              <i />
              <i />
            </div>
          ))}
        </div>
        <span className="mini-node">
          <Icon name="scales" size={18} />
        </span>
      </div>
      <ul>
        {notes.map((note) => (
          <li key={note}>
            <Icon name={parallel ? "check" : "close"} size={15} />
            {note}
          </li>
        ))}
      </ul>
    </article>
  );
}

/**
 * 渲染基准动机、串并行对照和由公开数据生成的六类任务统计。
 *
 * @param {{copy: object, dataset: object | null, language: "en" | "zh-CN"}} props - 本地化文案、公开数据集与当前语言。
 * @returns {React.ReactElement} Benchmark 段落。
 */
export function BenchmarkOverview({ copy, dataset, language }) {
  const groupCounts = dataset?.summary?.benchmark_group_counts ?? {};

  return (
    <section className="section section-benchmark" id="benchmark">
      <div className="site-shell">
        <div className="section-heading benchmark-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.lead}</p>
            <p>{copy.body}</p>
          </div>
        </div>

        <div className="comparison-grid">
          <ExecutionComparison
            mode="sequential"
            notes={copy.sequentialNotes}
            title={copy.sequential}
          />
          <ExecutionComparison mode="parallel" notes={copy.parallelNotes} title={copy.parallel} />
        </div>

        <div className="category-header">
          <h3>{copy.categoriesTitle}</h3>
          <p>{copy.categoriesNote}</p>
        </div>
        <div className="category-table">
          {canonicalGroupOrder.map((group) => (
            <div className="category-cell" key={group}>
              <Icon name={groupIcons[group]} size={22} />
              <span>
                <strong>{localizedValue(dataset, "benchmark_group", group, language)}</strong>
                <small>{groupCounts[group] ?? "—"}</small>
              </span>
            </div>
          ))}
        </div>

        <div className="preview-note">
          <Icon name="warning" size={18} />
          <span>{copy.previewNote}</span>
        </div>
      </div>
    </section>
  );
}
