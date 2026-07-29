import React from "react";

import { Icon } from "./Icon.jsx";

const laneIcons = ["browser", "desktop", "folder"];

/**
 * 绘制从规划器到多 Worker 再到评价器的并行执行示意图。
 *
 * @param {{copy: object}} props - Hero 区域的本地化文案。
 * @returns {React.ReactElement} 可响应式重排的语义化系统图。
 */
function ParallelExecutionDiagram({ copy }) {
  return (
    <div
      aria-label={`${copy.planner}, ${copy.lanes.map(([name]) => name).join(", ")}, ${copy.evaluator}`}
      className="hero-diagram"
      role="img"
    >
      <div className="diagram-phase-row" aria-hidden="true">
        <span>Plan</span>
        <span>{copy.checkpoints}</span>
        <span>Evaluate</span>
      </div>
      <div className="diagram-body">
        <div className="diagram-node diagram-endpoint planner-node">
          <Icon name="planner" size={26} />
          <strong>{copy.planner}</strong>
          <span>{copy.plannerDetail}</span>
        </div>
        <div className="worker-lanes">
          {copy.lanes.map(([name, detail], index) => (
            <div className={`worker-lane worker-lane-${index + 1}`} key={name}>
              <div className="lane-label">
                <Icon name={laneIcons[index]} size={19} />
                <span>
                  <strong>{name}</strong>
                  <small>{detail}</small>
                </span>
              </div>
              <div className="lane-track" aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
              </div>
            </div>
          ))}
        </div>
        <div className="diagram-node diagram-endpoint evaluator-node">
          <Icon name="scales" size={28} />
          <strong>{copy.evaluator}</strong>
          <span>{copy.evaluatorDetail}</span>
        </div>
      </div>
      <div className="diagram-time">
        <span>{copy.time}</span>
        <i aria-hidden="true" />
      </div>
    </div>
  );
}

/**
 * 渲染首页首屏、真实状态台账与并行执行主视觉。
 *
 * @param {{copy: object}} props - Hero 区域本地化文案。
 * @returns {React.ReactElement} 首页首屏内容。
 */
export function Hero({ copy }) {
  const statusIcons = ["box", "database", "check", "warning"];
  const statusClasses = ["preview", "definitions", "validated", "pending"];

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
        <ul className="status-ledger" aria-label={copy.status.join(", ")}>
          {copy.status.map((item, index) => (
            <li className={`status-${statusClasses[index]}`} key={item}>
              <Icon name={statusIcons[index]} size={18} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
      <ParallelExecutionDiagram copy={copy} />
    </section>
  );
}
