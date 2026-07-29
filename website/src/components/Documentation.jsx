import React from "react";

import { Icon } from "./Icon.jsx";

const documentIcons = ["code", "box", "database", "scales", "folder", "planner", "warning"];

/**
 * 渲染公开文档索引和运行记录的隐私边界说明。
 *
 * @param {{copy: object, links: Record<string, string>}} props - 文档文案与已知公开链接映射。
 * @returns {React.ReactElement} 文档链接矩阵。
 */
export function Documentation({ copy, links }) {
  return (
    <section className="section section-docs" id="docs">
      <div className="site-shell">
        <div className="section-heading docs-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.intro}</p>
          </div>
        </div>

        <div className="docs-table">
          {copy.links.map(([name, description, linkKey], index) => (
            <a href={links[linkKey]} key={name} rel="noreferrer" target="_blank">
              <Icon name={documentIcons[index]} size={20} />
              <strong>{name}</strong>
              <span>{description}</span>
              <small>
                {copy.view}
                <Icon name="arrow" size={16} />
              </small>
            </a>
          ))}
        </div>

        <div className="reproducibility-note">
          <Icon name="check" size={20} />
          <span>{copy.reproducibility}</span>
        </div>
      </div>
    </section>
  );
}
