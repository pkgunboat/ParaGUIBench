import React, { useState } from "react";

import { Icon } from "./Icon.jsx";

/**
 * 渲染 Core 与 OSWorld 两条安装路径，并提供不泄露凭据的命令复制。
 *
 * @param {{copy: object, installUrl: string, deploymentUrl: string}} props - 安装文案及公开文档链接。
 * @returns {React.ReactElement} 带键盘可用标签页和复制按钮的快速开始段落。
 */
export function Quickstart({ copy, installUrl, deploymentUrl }) {
  const [track, setTrack] = useState("core");
  const [copied, setCopied] = useState(false);
  const code = track === "core" ? copy.coreCode : copy.liveCode;

  /**
   * 将当前安装命令写入剪贴板并短暂显示确认状态。
   *
   * @returns {Promise<void>} 写入完成后更新界面；浏览器拒绝时保持原按钮文案。
   */
  async function copyCommands() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="section section-quickstart" id="quickstart">
      <div className="site-shell">
        <div className="section-heading quickstart-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.intro}</p>
          </div>
        </div>

        <div className="requirements-row">
          {copy.requirements.map((requirement, index) => (
            <span key={requirement}>
              <Icon name={index === 0 ? "code" : index === 1 ? "desktop" : "box"} size={18} />
              {requirement}
            </span>
          ))}
        </div>

        <div className="quickstart-grid">
          <div className="install-track">
            <div aria-label={copy.title} className="track-tabs" role="tablist">
              <button
                aria-controls="install-panel"
                aria-selected={track === "core"}
                className={track === "core" ? "active" : ""}
                onClick={() => setTrack("core")}
                role="tab"
                type="button"
              >
                <strong>{copy.core}</strong>
                <span>{copy.coreLabel}</span>
              </button>
              <button
                aria-controls="install-panel"
                aria-selected={track === "live"}
                className={track === "live" ? "active" : ""}
                onClick={() => setTrack("live")}
                role="tab"
                type="button"
              >
                <strong>{copy.live}</strong>
                <span>{copy.liveLabel}</span>
              </button>
            </div>

            <div className="code-panel" id="install-panel" role="tabpanel">
              <div className="code-panel-header">
                <span>
                  <Icon name="code" size={18} />
                  {track === "core" ? copy.coreLabel : copy.liveLabel}
                </span>
                <button onClick={copyCommands} type="button">
                  <Icon name={copied ? "check" : "copy"} size={17} />
                  {copied ? copy.copied : copy.copy}
                </button>
              </div>
              <pre>
                <code>{code}</code>
              </pre>
            </div>
          </div>

          <aside className="quickstart-notes">
            <div className="doctor-note">
              <Icon name="check" size={22} />
              <strong>{copy.doctor}</strong>
            </div>
            <p>{copy.secret}</p>
            <a className="doc-row-link" href={installUrl} rel="noreferrer" target="_blank">
              <span>{copy.guide}</span>
              <Icon name="arrow" size={18} />
            </a>
            <a className="doc-row-link" href={deploymentUrl} rel="noreferrer" target="_blank">
              <span>{copy.deployment}</span>
              <Icon name="arrow" size={18} />
            </a>
          </aside>
        </div>
      </div>
    </section>
  );
}
