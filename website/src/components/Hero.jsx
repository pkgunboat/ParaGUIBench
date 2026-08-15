import React from "react";

import { Icon } from "./Icon.jsx";
import { RuntimeArchitectureDiagram } from "./RuntimeArchitectureDiagram.jsx";

/**
 * 渲染首页首屏、真实状态台账与并行执行主视觉。
 *
 * @param {{copy: object, dataset: object | null}} props - Hero 区域本地化文案与公开 manifest 派生数据。
 * @returns {React.ReactElement} 首页首屏内容。
 */
export function Hero({ copy, dataset }) {
  const summary = dataset?.summary;
  const taskCount = summary?.task_count ?? "—";
  const localReadyCount = summary?.local_readiness_status_counts?.local_ready ?? "—";
  const localIncompleteCount =
    summary?.local_readiness_status_counts?.local_components_incomplete ?? "—";
  const liveCount = summary?.support_status_counts?.live_validated ?? "—";
  const blockedCount = summary?.support_status_counts?.blocked ?? "—";
  const statusItems = [
    copy.status.preview,
    `${taskCount} ${copy.status.canonical}`,
    `${localReadyCount} ${copy.status.localReady} · ${localIncompleteCount} ${copy.status.localIncomplete}`,
    `${liveCount} ${copy.status.live} · ${blockedCount} ${copy.status.blocked}`,
  ];
  const statusIcons = ["box", "database", "check", "warning"];
  const statusClasses = ["preview", "definitions", "local", "pending"];

  return (
    <section className="hero site-shell" id="top">
      <div className="hero-copy">
        <h1>{copy.title}</h1>
        <p>{copy.description}</p>
        <div className="hero-actions">
          <a className="button button-primary" href="#quickstart">
            {copy.primary}
          </a>
          <a className="text-link" href="#tasks">
            {copy.secondary}
            <Icon name="arrow" size={18} />
          </a>
        </div>
        <ul className="status-ledger" aria-label={statusItems.join(", ")}>
          {statusItems.map((item, index) => (
            <li className={`status-${statusClasses[index]}`} key={item}>
              <Icon name={statusIcons[index]} size={18} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
      <RuntimeArchitectureDiagram copy={copy.diagram} />
    </section>
  );
}
