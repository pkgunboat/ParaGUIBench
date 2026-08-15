import React from "react";

import { Icon } from "./Icon.jsx";

const capabilityIcons = ["browser", "desktop", "folder"];

/**
 * 渲染架构图中的统一流程节点。
 *
 * @param {{title: string, detail?: string, icon?: string, className?: string}} props - 节点标题、补充说明、可选图标和样式类。
 * @returns {React.ReactElement} 可被读屏器顺序读取的流程节点。
 */
function FlowNode({ title, detail, icon, className = "" }) {
  return (
    <div className={`runtime-flow-node ${className}`.trim()}>
      {icon ? <Icon name={icon} size={19} /> : null}
      <strong>{title}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

/**
 * 渲染相邻架构节点之间的方向箭头。
 *
 * @param {{label?: string, vertical?: boolean}} props - 可选关系标签以及是否使用纵向布局。
 * @returns {React.ReactElement} 不重复朗读的装饰性连接符。
 */
function FlowArrow({ label, vertical = false }) {
  return (
    <span
      aria-hidden="true"
      className={`runtime-flow-arrow${vertical ? " runtime-flow-arrow-vertical" : ""}`}
    >
      {label ? <small>{label}</small> : null}
      <i />
    </span>
  );
}

/**
 * 渲染环境内部可用的浏览器、桌面应用和任务资产能力。
 *
 * @param {{items: string[]}} props - 与图标顺序对应的本地化能力名称。
 * @returns {React.ReactElement} 环境资源列表；不会把资源误表示为 Worker。
 */
function CapabilityList({ items }) {
  return (
    <ul className="runtime-capabilities">
      {items.map((item, index) => (
        <li key={item}>
          <Icon name={capabilityIcons[index]} size={18} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * 渲染当前公开包保留的历史单 VM、单 Worker 冒烟执行链。
 *
 * @param {{copy: object}} props - 当前 preview 的本地化节点与状态文案。
 * @returns {React.ReactElement} 与论文多 Worker 系统严格分离的运行时面板。
 */
function CurrentPreviewPanel({ copy }) {
  return (
    <article className="runtime-panel runtime-panel-preview">
      <header className="runtime-panel-header">
        <div>
          <span className="runtime-panel-index">01</span>
          <h3>{copy.heading}</h3>
        </div>
        <span className="runtime-status runtime-status-pending">{copy.status}</span>
      </header>

      <div className="preview-runtime-flow">
        <FlowNode className="runtime-node-compact runtime-node-task" title={copy.task} />
        <FlowArrow />
        <FlowNode className="runtime-node-compact runtime-node-runner" title={copy.runner} />
        <FlowArrow />
        <div className="preview-environment">
          <div className="preview-environment-heading">
            <span>
              <strong>{copy.environment}</strong>
              <small>{copy.environmentMeta}</small>
            </span>
            <Icon name="desktop" size={21} />
          </div>
          <FlowNode
            className="preview-agent-node"
            detail={copy.agentMeta}
            icon="planner"
            title={copy.agent}
          />
          <CapabilityList items={copy.capabilities} />
        </div>
        <FlowArrow />
        <FlowNode
          className="runtime-node-compact runtime-node-evaluator"
          icon="scales"
          title={copy.evaluator}
        />
        <FlowArrow />
        <FlowNode
          className="runtime-node-compact runtime-node-store"
          icon="database"
          title={copy.runStore}
        />
      </div>

      <footer className="runtime-panel-footer">
        <span>{copy.scope}</span>
        <p>{copy.note}</p>
      </footer>
    </article>
  );
}

/**
 * 渲染一个可互换 GUI Worker 及其独占隔离桌面。
 *
 * @param {{copy: object, workerIndex: string}} props - Worker/环境公共文案与展示序号。
 * @returns {React.ReactElement} 通用 Worker 到隔离桌面的双向关系。
 */
function WorkerColumn({ copy, workerIndex }) {
  return (
    <div className="runtime-worker-column">
      <FlowNode
        className="runtime-worker-node"
        detail={copy.workerDetail}
        title={`${copy.worker} ${workerIndex}`}
      />
      <span aria-hidden="true" className="runtime-bidirectional-arrow">
        <i />
      </span>
      <div className="runtime-desktop-node">
        <Icon name="desktop" size={20} />
        <strong>{`${copy.environment} ${workerIndex}`}</strong>
        <small>{copy.environmentDetail}</small>
        <span className="runtime-environment-capability">
          <Icon name="browser" size={15} />
          {copy.environmentCapability}
        </span>
      </div>
    </div>
  );
}

/**
 * 渲染论文 ParaGUI 的自适应轮次式 planner–worker 参考架构。
 *
 * @param {{copy: object}} props - 论文系统、公开集成状态和评价边界文案。
 * @returns {React.ReactElement} 明确包含轮次回路、共享目录与 Agent 外评价器的面板。
 */
function ParaGUIReferencePanel({ copy }) {
  return (
    <article className="runtime-panel runtime-panel-reference">
      <header className="runtime-panel-header">
        <div>
          <span className="runtime-panel-index">02</span>
          <h3>{copy.heading}</h3>
        </div>
        <div className="runtime-reference-status">
          <span className="runtime-status runtime-status-paper">{copy.paperStatus}</span>
          <span className="runtime-status runtime-status-pending">{copy.packageStatus}</span>
        </div>
      </header>

      <div className="reference-runtime-flow">
        <FlowNode className="runtime-node-entry" title={copy.task} />
        <FlowArrow />

        <div className="runtime-agent-boundary">
          <div className="runtime-agent-boundary-heading">
            <strong>{copy.agentBoundary}</strong>
            <span>{copy.round}</span>
          </div>

          <div className="runtime-planner-row">
            <FlowNode
              className="runtime-planner-node"
              detail={copy.plannerDetail}
              icon="planner"
              title={copy.planner}
            />
            <div className="runtime-finish-decision">
              <span>{copy.decision}</span>
              <small>{copy.finishNo}</small>
            </div>
            <FlowNode
              className="runtime-final-node"
              detail={copy.finishYes}
              title={copy.finalOutput}
            />
          </div>

          <div className="runtime-dispatch-label">
            <span>{copy.dispatch}</span>
            <i aria-hidden="true" />
          </div>

          <div className="runtime-worker-grid">
            {copy.workerIndexes.map((workerIndex) => (
              <WorkerColumn copy={copy} key={workerIndex} workerIndex={workerIndex} />
            ))}
          </div>

          <div className="runtime-shared-directory">
            <Icon name="folder" size={18} />
            <span>
              <strong>{copy.sharedDirectory}</strong>
              <small>{copy.sharedDirectoryDetail}</small>
            </span>
          </div>

          <div className="runtime-round-return">
            <FlowNode
              className="runtime-barrier-node"
              detail={copy.barrierDetail}
              title={copy.barrier}
            />
            <div className="runtime-loop-copy">
              <span>{copy.history}</span>
              <Icon name="arrow" size={17} />
              <span>{copy.nextRound}</span>
            </div>
          </div>

          <p className="runtime-worker-note">{copy.workerNote}</p>
        </div>

        <FlowArrow />
        <div className="runtime-evaluation-stage">
          <div className="runtime-evaluation-inputs">
            <span>{copy.answerInput}</span>
            <span>{copy.environmentInput}</span>
          </div>
          <FlowNode
            className="runtime-evaluator-node"
            detail={copy.evaluatorDetail}
            icon="scales"
            title={copy.evaluator}
          />
        </div>
        <FlowArrow />
        <FlowNode
          className="runtime-node-entry runtime-node-store"
          icon="database"
          title={copy.runStore}
        />
      </div>
    </article>
  );
}

/**
 * 渲染首屏中的双面板运行状态与论文架构对照图。
 *
 * @param {{copy: object}} props - 完整双语架构图文案。
 * @returns {React.ReactElement} 桌面双栏、窄屏堆叠的语义化网页图。
 */
export function RuntimeArchitectureDiagram({ copy }) {
  return (
    <figure
      aria-label={copy.accessibleLabel}
      className="runtime-architecture"
      id="runtime-architecture"
    >
      <figcaption className="runtime-architecture-heading">
        <span>{copy.label}</span>
        <h2>{copy.title}</h2>
        <p>{copy.intro}</p>
      </figcaption>

      <div className="runtime-panels">
        <CurrentPreviewPanel copy={copy.preview} />
        <ParaGUIReferencePanel copy={copy.reference} />
      </div>

      <ul className="runtime-clarifications">
        {copy.notes.map((note, index) => (
          <li key={note}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            {note}
          </li>
        ))}
      </ul>
    </figure>
  );
}
