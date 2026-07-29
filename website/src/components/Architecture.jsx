import React from "react";

import { Icon } from "./Icon.jsx";

const layerIcons = ["box", "code", "scales"];

/**
 * 渲染单层架构模块及其内部依赖方向。
 *
 * @param {{layer: {name: string, description: string, nodes: string[]}, icon: string, index: number}} props - 层名称、职责、节点和视觉序号。
 * @returns {React.ReactElement} 一行架构层。
 */
function ArchitectureLayer({ layer, icon, index }) {
  return (
    <div className={`architecture-layer architecture-layer-${index + 1}`}>
      <div className="layer-label">
        <Icon name={icon} size={26} />
        <span>
          <strong>{layer.name}</strong>
          <small>{layer.description}</small>
        </span>
      </div>
      <div className="layer-nodes">
        {layer.nodes.map((node, nodeIndex) => (
          <React.Fragment key={node}>
            <span className="architecture-node">{node}</span>
            {nodeIndex < layer.nodes.length - 1 ? (
              <Icon className="node-arrow" name="arrow" size={17} />
            ) : null}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

/**
 * 渲染 Framework、Agent Systems 与 Evaluation 的分层架构。
 *
 * @param {{copy: object, guideUrl: string}} props - 架构文案和公开说明链接。
 * @returns {React.ReactElement} 架构段落与多机器运行上下文示意。
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

        <div className="architecture-board">
          <div className="architecture-stack">
            {copy.layers.map((layer, index) => (
              <ArchitectureLayer
                icon={layerIcons[index]}
                index={index}
                key={layer.name}
                layer={layer}
              />
            ))}
          </div>

          <div className="machine-grid">
            {copy.machines.map((machine, index) => (
              <div className="machine-node" key={machine}>
                <Icon name="desktop" size={18} />
                <strong>{machine}</strong>
                <div className="machine-rail" aria-hidden="true">
                  <i />
                  <i className={index === 1 ? "active" : ""} />
                  <i />
                </div>
              </div>
            ))}
          </div>
          <div className="shared-run-context">
            <Icon name="database" size={18} />
            <span>{copy.runId}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
