#!/usr/bin/env node

import { lstat, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const HTML_REFERENCE_PATTERN =
  /\b(?:href|src|poster|action)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/giu;
const HTML_SRCSET_PATTERN =
  /\bsrcset\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/giu;
const CSS_URL_PATTERN =
  /\burl\(\s*(?:"([^"]*)"|'([^']*)'|([^'")\s]+))\s*\)/giu;
const EXTERNAL_SCHEME_PATTERN = /^[a-z][a-z0-9+.-]*:/iu;
const ALLOWED_EXTERNAL_SCHEME_PATTERN =
  /^(?:https?|mailto|tel|data|blob):/iu;
const FORBIDDEN_SCHEME_PATTERN = /^(?:javascript|vbscript):/iu;

/**
 * 表示静态 Pages 产物不满足发布契约。
 */
export class StaticSiteValidationError extends Error {
  /**
   * 创建可由 CLI 安全显示的校验错误。
   *
   * 输入参数：
   *   message：不包含凭据或宿主机绝对路径的错误说明。
   * 输出返回值：
   *   新建的 StaticSiteValidationError 实例。
   */
  constructor(message) {
    super(message);
    this.name = "StaticSiteValidationError";
  }
}

/**
 * 校验 GitHub Pages project site 使用的 base path 格式。
 *
 * 输入参数：
 *   basePath：预期形如 ``/ParaGUIBench/`` 的站点路径前缀。
 * 输出返回值：
 *   保持原大小写的规范 base path；格式错误时抛出校验异常。
 */
function normalizeBasePath(basePath) {
  if (
    typeof basePath !== "string" ||
    !basePath.startsWith("/") ||
    !basePath.endsWith("/") ||
    basePath === "/" ||
    basePath.includes("\\") ||
    basePath.includes("?") ||
    basePath.includes("#") ||
    basePath.includes("//")
  ) {
    throw new StaticSiteValidationError(
      "Pages base path 必须是非根目录、首尾均为斜杠的规范路径",
    );
  }

  const segments = basePath.slice(1, -1).split("/");
  if (
    segments.some(
      (segment) =>
        segment.length === 0 ||
        segment === "." ||
        segment === ".." ||
        decodeSafely(segment) !== segment,
    )
  ) {
    throw new StaticSiteValidationError(
      "Pages base path 不得包含空段、转义字符或路径穿越段",
    );
  }
  return basePath;
}

/**
 * 对单个 URL 路径片段执行一次安全解码。
 *
 * 输入参数：
 *   value：可能含百分号编码的字符串。
 * 输出返回值：
 *   解码结果；编码非法时抛出静态站点校验异常。
 */
function decodeSafely(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    throw new StaticSiteValidationError("静态链接包含非法百分号编码");
  }
}

/**
 * 递归枚举产物目录，并拒绝符号链接或特殊文件。
 *
 * 输入参数：
 *   root：静态产物根目录绝对路径。
 *   current：当前递归目录绝对路径。
 *   files：用于收集 POSIX 相对路径的数组。
 * 输出返回值：
 *   无；函数原地填充 files。
 */
async function collectArtifactFiles(root, current, files) {
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));

  for (const entry of entries) {
    const absolutePath = path.join(current, entry.name);
    const metadata = await lstat(absolutePath);
    const relativePath = path
      .relative(root, absolutePath)
      .split(path.sep)
      .join(path.posix.sep);

    if (metadata.isSymbolicLink()) {
      throw new StaticSiteValidationError(
        `静态产物不得包含符号链接：${relativePath}`,
      );
    }
    if (metadata.isDirectory()) {
      await collectArtifactFiles(root, absolutePath, files);
      continue;
    }
    if (!metadata.isFile()) {
      throw new StaticSiteValidationError(
        `静态产物不得包含特殊文件：${relativePath}`,
      );
    }
    if (metadata.nlink > 1) {
      throw new StaticSiteValidationError(
        `Pages artifact 不得包含硬链接：${relativePath}`,
      );
    }
    files.push(relativePath);
  }
}

/**
 * 从 HTML 属性中提取 href、src、poster、action 与 srcset 引用。
 *
 * 输入参数：
 *   content：HTML 文件文本。
 * 输出返回值：
 *   页面中声明的 URL 字符串列表。
 */
function extractHtmlReferences(content) {
  const references = [];
  for (const match of content.matchAll(HTML_REFERENCE_PATTERN)) {
    references.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  for (const match of content.matchAll(HTML_SRCSET_PATTERN)) {
    const srcset = match[1] ?? match[2] ?? match[3] ?? "";
    for (const candidate of srcset.split(",")) {
      const url = candidate.trim().split(/\s+/u)[0];
      if (url) {
        references.push(url);
      }
    }
  }
  return references;
}

/**
 * 从 CSS 的 url(...) 声明中提取静态资源引用。
 *
 * 输入参数：
 *   content：CSS 文件文本。
 * 输出返回值：
 *   样式表引用的 URL 字符串列表。
 */
function extractCssReferences(content) {
  const references = [];
  for (const match of content.matchAll(CSS_URL_PATTERN)) {
    references.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return references;
}

/**
 * 将少量 HTML URL 实体还原为链接校验需要的原始字符。
 *
 * 输入参数：
 *   value：来自 HTML 属性的 URL 文本。
 * 输出返回值：
 *   还原 amp、quot、apos、lt、gt 后的字符串。
 */
function decodeHtmlUrlEntities(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">");
}

/**
 * 判断引用是否由浏览器在站点之外解析，因此无需检查本地产物。
 *
 * 输入参数：
 *   reference：已去除首尾空白的 URL。
 * 输出返回值：
 *   外部协议、data URL 或协议相对地址返回 true，否则返回 false。
 */
function isExternalReference(reference) {
  return reference.startsWith("//") || EXTERNAL_SCHEME_PATTERN.test(reference);
}

/**
 * 将站点内 URL 映射为产物根目录内的 POSIX 相对路径。
 *
 * 输入参数：
 *   reference：HTML 或 CSS 中发现的原始 URL。
 *   sourcePath：引用所在文件相对产物根目录的 POSIX 路径。
 *   basePath：已规范化的 GitHub Pages project site 前缀。
 * 输出返回值：
 *   外部或纯锚点链接返回 null；内部引用返回相对目标路径。
 */
function resolveInternalReference(reference, sourcePath, basePath) {
  const decodedReference = decodeHtmlUrlEntities(reference.trim());
  if (!decodedReference || decodedReference.startsWith("#")) {
    return null;
  }
  if (FORBIDDEN_SCHEME_PATTERN.test(decodedReference)) {
    throw new StaticSiteValidationError(
      `静态链接不得使用可执行协议：${sourcePath}`,
    );
  }
  if (
    EXTERNAL_SCHEME_PATTERN.test(decodedReference) &&
    !ALLOWED_EXTERNAL_SCHEME_PATTERN.test(decodedReference)
  ) {
    throw new StaticSiteValidationError(
      `静态链接使用了不支持的协议：${sourcePath}`,
    );
  }
  if (isExternalReference(decodedReference)) {
    return null;
  }
  if (decodedReference.includes("\\")) {
    throw new StaticSiteValidationError(
      `静态链接不得使用反斜杠：${sourcePath}`,
    );
  }

  const pathOnly = decodedReference.split(/[?#]/u, 1)[0];
  if (!pathOnly) {
    return sourcePath;
  }
  const decodedPath = decodeSafely(pathOnly);
  let targetPath;

  if (decodedPath.startsWith("/")) {
    if (!decodedPath.startsWith(basePath)) {
      throw new StaticSiteValidationError(
        `根绝对链接必须以 ${basePath} 开头：${sourcePath}`,
      );
    }
    targetPath = decodedPath.slice(basePath.length);
  } else {
    targetPath = path.posix.join(path.posix.dirname(sourcePath), decodedPath);
  }

  const normalizedTarget = path.posix.normalize(targetPath);
  if (
    normalizedTarget === ".." ||
    normalizedTarget.startsWith("../") ||
    path.posix.isAbsolute(normalizedTarget)
  ) {
    throw new StaticSiteValidationError(
      `静态链接不得越过产物根目录：${sourcePath}`,
    );
  }

  if (!normalizedTarget || normalizedTarget === ".") {
    return "index.html";
  }
  if (decodedPath.endsWith("/")) {
    return path.posix.join(normalizedTarget, "index.html");
  }
  return normalizedTarget;
}

/**
 * 断言内部引用可由静态产物直接解析。
 *
 * 输入参数：
 *   targetPath：已解析的产物内相对目标。
 *   sourcePath：引用来源文件相对路径。
 *   files：全部产物文件的集合。
 * 输出返回值：
 *   无；目标不存在时抛出校验异常。
 */
function requireReferenceTarget(targetPath, sourcePath, files) {
  const candidates = [
    targetPath,
    `${targetPath}.html`,
    path.posix.join(targetPath, "index.html"),
  ];
  if (!candidates.some((candidate) => files.has(candidate))) {
    throw new StaticSiteValidationError(
      `内部链接目标不存在：${sourcePath} -> ${targetPath}`,
    );
  }
}

/**
 * 校验一份 GitHub Pages 静态产物的入口、base path、链接与文件类型。
 *
 * 输入参数：
 *   artifactRoot：待发布的静态目录路径。
 *   expectedBasePath：project site 的固定路径前缀。
 * 输出返回值：
 *   包含 basePath、文件数和检查引用数的只读汇总对象。
 */
export async function validateStaticSite(
  artifactRoot,
  expectedBasePath = "/ParaGUIBench/",
) {
  const basePath = normalizeBasePath(expectedBasePath);
  const root = path.resolve(artifactRoot);
  let rootMetadata;
  try {
    rootMetadata = await lstat(root);
  } catch {
    throw new StaticSiteValidationError("静态产物目录不存在或不可读");
  }
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new StaticSiteValidationError(
      "静态产物根路径必须是非符号链接目录",
    );
  }

  const artifactFiles = [];
  await collectArtifactFiles(root, root, artifactFiles);
  const files = new Set(artifactFiles);
  for (const entryFile of ["index.html", "404.html"]) {
    if (!files.has(entryFile)) {
      throw new StaticSiteValidationError(
        `静态产物缺少必需入口文件：${entryFile}`,
      );
    }
  }

  let referenceCount = 0;
  for (const sourcePath of artifactFiles) {
    const extension = path.posix.extname(sourcePath).toLowerCase();
    if (extension !== ".html" && extension !== ".css") {
      continue;
    }
    const content = await readFile(path.join(root, sourcePath), "utf8");
    const references =
      extension === ".html"
        ? extractHtmlReferences(content)
        : extractCssReferences(content);
    for (const reference of references) {
      referenceCount += 1;
      const targetPath = resolveInternalReference(
        reference,
        sourcePath,
        basePath,
      );
      if (targetPath !== null) {
        requireReferenceTarget(targetPath, sourcePath, files);
      }
    }
  }

  return Object.freeze({
    basePath,
    fileCount: artifactFiles.length,
    referenceCount,
  });
}

/**
 * 解析命令行参数并执行产物门禁。
 *
 * 输入参数：
 *   argv：不含 node 和脚本路径的命令行参数。
 * 输出返回值：
 *   校验成功返回 0，输入或产物不合格返回 1。
 */
async function runCli(argv) {
  let artifactRoot = null;
  let basePath = "/ParaGUIBench/";
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--base") {
      basePath = argv[index + 1] ?? "";
      index += 1;
      continue;
    }
    if (argument.startsWith("--")) {
      console.error(`未知参数：${argument}`);
      return 1;
    }
    if (artifactRoot !== null) {
      console.error("只能提供一个静态产物目录");
      return 1;
    }
    artifactRoot = argument;
  }
  if (artifactRoot === null) {
    console.error(
      "用法：node validate-static-site.mjs <artifact-root> [--base /ParaGUIBench/]",
    );
    return 1;
  }

  try {
    const result = await validateStaticSite(artifactRoot, basePath);
    console.log(
      `静态产物检查通过：${result.fileCount} 个文件，` +
        `${result.referenceCount} 个引用，base=${result.basePath}`,
    );
    return 0;
  } catch (error) {
    if (error instanceof StaticSiteValidationError) {
      console.error(`静态产物检查失败：${error.message}`);
      return 1;
    }
    console.error("静态产物检查失败：发生未预期错误");
    return 1;
  }
}

const invokedAsScript =
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedAsScript) {
  process.exitCode = await runCli(process.argv.slice(2));
}
