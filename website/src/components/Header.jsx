import React, { useEffect, useState } from "react";

import { Icon } from "./Icon.jsx";

/**
 * 渲染桌面与移动端共用的站点导航。
 *
 * @param {{copy: object, language: "en" | "zh-CN", onLanguageToggle: () => void, repositoryUrl: string}} props - 当前语言文案、语言切换回调与仓库地址。
 * @returns {React.ReactElement} 可键盘操作并支持 Escape 关闭的导航栏。
 */
export function Header({ copy, language, onLanguageToggle, repositoryUrl }) {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    /**
     * 在移动菜单展开时响应 Escape，避免键盘用户被困在导航区域。
     *
     * @param {KeyboardEvent} event - 浏览器键盘事件。
     * @returns {void}
     */
    function closeOnEscape(event) {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  /**
   * 点击锚点后收起移动菜单。
   *
   * @returns {void}
   */
  function closeMenu() {
    setMenuOpen(false);
  }

  return (
    <header className="site-header">
      <div className="site-shell header-inner">
        <a className="wordmark" href="#top" onClick={closeMenu}>
          <span className="wordmark-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          ParaGUIBench
        </a>

        <button
          aria-expanded={menuOpen}
          aria-label={menuOpen ? copy.menuClose : copy.menuOpen}
          className="menu-button"
          onClick={() => setMenuOpen((current) => !current)}
          type="button"
        >
          <Icon name={menuOpen ? "close" : "menu"} size={24} />
        </button>

        <nav
          aria-label={copy.navigationLabel}
          className={`site-nav ${menuOpen ? "site-nav-open" : ""}`}
        >
          <div className="nav-links">
            {copy.nav.map(([label, target]) => (
              <a href={`#${target}`} key={target} onClick={closeMenu}>
                {label}
              </a>
            ))}
          </div>
          <div className="nav-actions">
            <button
              aria-label={`${copy.languageName} / ${copy.languageToggle}`}
              className="language-button"
              onClick={onLanguageToggle}
              type="button"
            >
              <span className={language === "en" ? "active-language" : ""}>EN</span>
              <span aria-hidden="true">/</span>
              <span className={language === "zh-CN" ? "active-language" : ""}>中</span>
            </button>
            <a className="github-button" href={repositoryUrl} rel="noreferrer" target="_blank">
              <Icon name="github" size={18} />
              {copy.github}
            </a>
          </div>
        </nav>
      </div>
    </header>
  );
}
