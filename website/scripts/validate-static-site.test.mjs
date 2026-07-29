import assert from "node:assert/strict";
import { link, mkdir, mkdtemp, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  StaticSiteValidationError,
  validateStaticSite,
} from "./validate-static-site.mjs";

/**
 * 创建一个满足 ParaGUIBench project site 约束的最小静态产物。
 *
 * 输入参数：
 *   root：测试产物的根目录。
 * 输出返回值：
 *   无；完成后目录中包含 index、404、JS、CSS 与公共数据文件。
 */
async function createValidArtifact(root) {
  await mkdir(path.join(root, "assets"), { recursive: true });
  await mkdir(path.join(root, "data"), { recursive: true });
  const html = `<!doctype html>
<html lang="zh-CN">
  <head>
    <link rel="stylesheet" href="/ParaGUIBench/assets/app.css">
  </head>
  <body>
    <a href="/ParaGUIBench/#quickstart">Quickstart</a>
    <script type="module" src="/ParaGUIBench/assets/app.js"></script>
  </body>
</html>
`;
  await writeFile(path.join(root, "index.html"), html, "utf8");
  await writeFile(path.join(root, "404.html"), html, "utf8");
  await writeFile(path.join(root, "assets", "app.js"), "export {};\n", "utf8");
  await writeFile(
    path.join(root, "assets", "app.css"),
    "body { background: url('../data/grid.svg'); }\n",
    "utf8",
  );
  await writeFile(path.join(root, "data", "grid.svg"), "<svg></svg>\n", "utf8");
  await writeFile(path.join(root, "data", "site-data.json"), "{}\n", "utf8");
}

/**
 * 为单个测试创建隔离目录，避免门禁用例污染工作区。
 *
 * 输入参数：
 *   无。
 * 输出返回值：
 *   新建的临时目录绝对路径。
 */
async function createTemporaryArtifactRoot() {
  return mkdtemp(path.join(tmpdir(), "paraguibench-pages-"));
}

test("接受完整且兼容 /ParaGUIBench/ 的静态产物", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);

  const result = await validateStaticSite(root, "/ParaGUIBench/");

  assert.equal(result.basePath, "/ParaGUIBench/");
  assert.equal(result.fileCount, 6);
  assert.equal(result.referenceCount, 7);
});

test("拒绝缺失 404.html 的产物", async () => {
  const root = await createTemporaryArtifactRoot();
  await mkdir(root, { recursive: true });
  await writeFile(path.join(root, "index.html"), "<!doctype html>\n", "utf8");

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("404.html"),
  );
});

test("拒绝绕过 project site 前缀的根绝对链接", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);
  await writeFile(
    path.join(root, "index.html"),
    '<script type="module" src="/assets/app.js"></script>\n',
    "utf8",
  );

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("/ParaGUIBench/"),
  );
});

test("拒绝指向不存在文件的内部链接", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);
  await writeFile(
    path.join(root, "index.html"),
    '<link rel="stylesheet" href="/ParaGUIBench/assets/missing.css">\n',
    "utf8",
  );

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("不存在"),
  );
});

test("拒绝 file URL 等不可公开协议", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);
  await writeFile(
    path.join(root, "index.html"),
    '<a href="file:///private/local-report.html">Local report</a>\n',
    "utf8",
  );

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("不支持的协议"),
  );
});

test("拒绝静态产物中的符号链接", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);
  await symlink(
    path.join(root, "assets", "app.js"),
    path.join(root, "assets", "linked.js"),
  );

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("符号链接"),
  );
});

test("拒绝 Pages artifact 不支持的硬链接", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);
  await link(
    path.join(root, "assets", "app.js"),
    path.join(root, "assets", "hard-linked.js"),
  );

  await assert.rejects(
    validateStaticSite(root, "/ParaGUIBench/"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("硬链接"),
  );
});

test("拒绝不规范的 Pages base path", async () => {
  const root = await createTemporaryArtifactRoot();
  await createValidArtifact(root);

  await assert.rejects(
    validateStaticSite(root, "ParaGUIBench"),
    (error) =>
      error instanceof StaticSiteValidationError &&
      error.message.includes("base path"),
  );
});
