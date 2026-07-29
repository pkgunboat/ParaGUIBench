import React from "react";

import { Icon } from "./Icon.jsx";

/**
 * 渲染站点页脚、公开仓库链接与引用状态。
 *
 * @param {{copy: object, repositoryUrl: string, licenseUrl: string, languageLabel: string, onLanguageToggle: () => void}} props - 页脚文案、公开链接和语言切换。
 * @returns {React.ReactElement} 无分析脚本、无外部资源的页脚。
 */
export function Footer({
  copy,
  repositoryUrl,
  licenseUrl,
  languageLabel,
  onLanguageToggle,
}) {
  return (
    <footer className="site-footer">
      <div className="site-shell footer-grid">
        <div className="footer-brand">
          <a className="wordmark" href="#top">
            <span className="wordmark-mark" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            ParaGUIBench
          </a>
          <p>{copy.description}</p>
        </div>
        <div className="footer-links">
          <a href={repositoryUrl} rel="noreferrer" target="_blank">
            <Icon name="github" size={18} />
            GitHub
          </a>
          <a href="#docs">
            <Icon name="folder" size={18} />
            Docs
          </a>
          <a href={licenseUrl} rel="noreferrer" target="_blank">
            <Icon name="scales" size={18} />
            License
          </a>
        </div>
        <div className="footer-meta">
          <button onClick={onLanguageToggle} type="button">
            {languageLabel}
          </button>
          <span>{copy.citation}</span>
          <small>{copy.preview}</small>
        </div>
      </div>
    </footer>
  );
}
