import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * 规范化 GitHub Pages 的项目子路径。
 *
 * @param {string | undefined} rawBase - 工作流或本地开发传入的原始路径。
 * @returns {string} 以斜杠开头和结尾的 Vite base 路径。
 */
function normalizeBasePath(rawBase) {
  const candidate = rawBase?.trim() || "/ParaGUIBench/";
  return `/${candidate.replace(/^\/+|\/+$/g, "")}/`;
}

export default defineConfig({
  base: normalizeBasePath(process.env.PARAGUIBENCH_SITE_BASE),
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
