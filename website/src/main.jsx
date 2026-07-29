import React from "react";
import { createRoot } from "react-dom/client";

import App from "./App.jsx";
import "./styles.css";

/**
 * 将 React 应用挂载到静态 HTML 根节点。
 *
 * @returns {void}
 */
function bootstrap() {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("ParaGUIBench site root element is missing");
  }
  createRoot(rootElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

bootstrap();
