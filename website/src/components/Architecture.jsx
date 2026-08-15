import React from "react";

import { Icon } from "./Icon.jsx";

const moduleIcons = ["database", "box", "planner", "code", "desktop", "scales", "folder"];

/**
 * 渲染一个公开包模块的所有权边界与职责节点。
 *
 * @param {{module: {name: string, description: string, nodes: string[]}, icon: string, index: number}} props - 模块文案、图标和视觉序号。
 * @returns {React.ReactElement} 不暗示运行顺序的模块边界卡片。
 */
function ArchitectureModule({ module, icon, index }) {
  return (
    <article className={`module-card module-card-${(index % 3) + 1}`}>
      <header>
        <span className="module-card-icon">
          <Icon name={icon} size={22} />
        </span>
        <span>
          <strong>{module.name}</strong>
          <small>{module.description}</small>
        </span>
      </header>
      <ul>
        {module.nodes.map((node) => (
          <li key={node}>{node}</li>
        ))}
      </ul>
    </article>
  );
}

/**
 * 渲染公开仓库的模块职责与依赖禁区，而非论文 Agent 的执行流程。
 *
 * @param {{copy: object, guideUrl: string}} props - 模块文案和公开说明链接。
 * @returns {React.ReactElement} 模块所有权卡片、边界规则和架构文档入口。
 */
export function Architecture({ copy, guideUrl }) {
  return (
    <section className="section section-architecture" id="architecture">
      <div className="site-shell">
        <div className="section-heading architecture-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.intro}</p>
            <a className="text-link" href={guideUrl} rel="noreferrer" target="_blank">
              {copy.link}
              <Icon name="arrow" size={18} />
            </a>
          </div>
        </div>

        <div className="module-architecture-board">
          <div className="module-card-grid">
            {copy.modules.map((module, index) => (
              <ArchitectureModule
                icon={moduleIcons[index]}
                index={index}
                key={module.name}
                module={module}
              />
            ))}
          </div>

          <aside className="module-boundaries" aria-label={copy.boundaryLabel}>
            <div className="module-boundaries-heading">
              <Icon name="warning" size={20} />
              <strong>{copy.boundaryTitle}</strong>
            </div>
            <ul>
              {copy.boundaries.map((boundary) => (
                <li key={boundary}>{boundary}</li>
              ))}
            </ul>
          </aside>
        </div>
      </div>
    </section>
  );
}
