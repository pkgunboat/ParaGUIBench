import React, { useEffect, useMemo, useState } from "react";

import { Architecture } from "./components/Architecture.jsx";
import { BenchmarkOverview } from "./components/BenchmarkOverview.jsx";
import { Documentation } from "./components/Documentation.jsx";
import { Footer } from "./components/Footer.jsx";
import { Header } from "./components/Header.jsx";
import { Hero } from "./components/Hero.jsx";
import { Quickstart } from "./components/Quickstart.jsx";
import { Results } from "./components/Results.jsx";
import { TaskExplorer } from "./components/TaskExplorer.jsx";
import { content, sharedLinks } from "./content.js";

const LANGUAGE_STORAGE_KEY = "paraguibench-language";

/**
 * 读取本地语言偏好；未设置时使用英文作为国际化项目站默认语言。
 *
 * @returns {"en" | "zh-CN"} 受支持的语言代码。
 */
function readInitialLanguage() {
  try {
    const storedLanguage = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return storedLanguage === "zh-CN" ? "zh-CN" : "en";
  } catch {
    return "en";
  }
}

/**
 * 从 Vite base 路径加载经安全投影的公开任务数据。
 *
 * @param {AbortSignal} signal - 页面卸载时用于取消请求的信号。
 * @returns {Promise<object>} 已解析的站点数据。
 */
async function loadSiteData(signal) {
  const response = await fetch(`${import.meta.env.BASE_URL}data/site-data.json`, { signal });
  if (!response.ok) {
    throw new Error(`site data request failed with status ${response.status}`);
  }
  return response.json();
}

/**
 * 组合 ParaGUIBench 单页项目站并管理语言与公开数据加载状态。
 *
 * @returns {React.ReactElement} 完整静态站点应用。
 */
export default function App() {
  const [language, setLanguage] = useState(readInitialLanguage);
  const [dataset, setDataset] = useState(null);
  const [dataError, setDataError] = useState(false);
  const copy = useMemo(() => content[language], [language]);

  useEffect(() => {
    const controller = new AbortController();
    setDataError(false);
    loadSiteData(controller.signal)
      .then(setDataset)
      .catch((error) => {
        if (error.name !== "AbortError") {
          setDataError(true);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch {
      // 隐私模式可能禁止 localStorage；语言切换仍在当前会话中有效。
    }
  }, [language]);

  /**
   * 在英文和简体中文之间切换界面语言。
   *
   * @returns {void}
   */
  function toggleLanguage() {
    setLanguage((current) => (current === "en" ? "zh-CN" : "en"));
  }

  return (
    <>
      <Header
        copy={copy}
        language={language}
        onLanguageToggle={toggleLanguage}
        repositoryUrl={sharedLinks.repository}
      />
      <main id="main-content">
        <Hero copy={copy.hero} />
        <BenchmarkOverview copy={copy.benchmark} dataset={dataset} language={language} />
        <Architecture copy={copy.architecture} guideUrl={sharedLinks.architecture} />
        <TaskExplorer
          copy={copy.tasks}
          dataset={dataset}
          error={dataError}
          language={language}
          loading={!dataset && !dataError}
        />
        <Quickstart
          copy={copy.quickstart}
          deploymentUrl={sharedLinks.deployment}
          installUrl={sharedLinks.install}
        />
        <Results copy={copy.results} dataset={dataset} />
        <Documentation copy={copy.docs} links={sharedLinks} />
      </main>
      <Footer
        copy={copy.footer}
        languageLabel={`${copy.languageName} / ${copy.languageToggle}`}
        licenseUrl={sharedLinks.license}
        onLanguageToggle={toggleLanguage}
        repositoryUrl={sharedLinks.repository}
      />
    </>
  );
}
