#!/usr/bin/env node
/**
 * 语义 token CSS 生成器(theme.md §2.3 单一事实源)。
 *
 * 从 `src/design/tokenValues.ts`(唯一事实源)生成三个产物:
 * - `src/design/tokens.css`        `:root` 亮色全量(分组注释来自 LIGHT_TOKEN_GROUPS)
 * - `src/design/tokens-dark.css`   `:root[data-theme='dark']` 暗色整组替换
 * - `src/design/tokens-print.css`  `@media print` 强制亮色(仅颜色 token,取亮色值)
 *
 * 产物首行一律带「禁止手改」标记;CI 幂等断言:重跑本脚本后工作区无 diff。
 * 改 token 只改 tokenValues.ts,随后 `npm run gen:tokens`(或 `npm run build` 自动串联)。
 *
 * tokenValues.ts 是 TypeScript,node 无法直接 import —— 经 esbuild(vite 传递依赖,
 * 已在 devDependencies 显式固定)buildSync 转译为临时 ESM 再动态 import。
 */
import { buildSync } from 'esbuild';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE_REL = 'src/design/tokenValues.ts';
const OUT_DIR = path.join(ROOT, 'src/design');

/** 生成产物首行标记(tokens.test.ts 断言此常量,两处须一致)。 */
export const GENERATED_HEADER =
  '/* 本文件由 scripts/gen-tokens.mjs 从 src/design/tokenValues.ts 生成 —— 禁止手改。 */';

/**
 * 经 esbuild 转译 TS 源码为临时 ESM 再动态 import(避免引入 tsx 等运行时依赖)。
 * `buildOptions` 透传给 buildSync(bundle/entryPoints/stdin 等);默认单文件不打包。
 */
export async function transpileImport(buildOptions) {
  const tmpDir = mkdtempSync(path.join(tmpdir(), 'mesh-tokens-'));
  const outFile = path.join(tmpDir, 'module.mjs');
  try {
    buildSync({
      bundle: false,
      format: 'esm',
      platform: 'node',
      logLevel: 'silent',
      outfile: outFile,
      ...buildOptions,
    });
    return await import(pathToFileURL(outFile).href);
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

/** 导入 token 单一事实源(tokenValues.ts)。 */
export async function loadTokenValues(sourceAbs = path.join(ROOT, SOURCE_REL)) {
  return transpileImport({ entryPoints: [sourceAbs] });
}

/** 渲染亮色文件:`:root { ... }` 全量,分组注释与 tokenValues.ts 的分组一致。 */
export function renderLightCss(groups) {
  const lines = [GENERATED_HEADER, ':root {'];
  for (const group of groups) {
    if (lines.length > 2) lines.push('');
    lines.push(`  /* ${group.title} */`);
    for (const [name, value] of Object.entries(group.tokens)) {
      lines.push(`  ${name}: ${value};`);
    }
  }
  lines.push('}', '');
  return lines.join('\n');
}

/** 渲染暗色文件:`:root[data-theme='dark'] { ... }` 整组替换。 */
export function renderDarkCss(darkTokens) {
  const lines = [GENERATED_HEADER, ":root[data-theme='dark'] {"];
  for (const [name, value] of Object.entries(darkTokens)) {
    lines.push(`  ${name}: ${value};`);
  }
  lines.push('}', '');
  return lines.join('\n');
}

/** 渲染打印文件:仅颜色 token(--color-*),一律取亮色值(打印强制亮色,theme.md §4.3)。 */
export function renderPrintCss(lightTokens) {
  const lines = [GENERATED_HEADER, '@media print {', "  :root, :root[data-theme='dark'] {"];
  for (const [name, value] of Object.entries(lightTokens)) {
    if (!name.startsWith('--color-')) continue;
    lines.push(`    ${name}: ${value};`);
  }
  lines.push('  }', '}', '');
  return lines.join('\n');
}

async function main() {
  const mod = await loadTokenValues();
  const files = [
    [path.join(OUT_DIR, 'tokens.css'), renderLightCss(mod.LIGHT_TOKEN_GROUPS)],
    [path.join(OUT_DIR, 'tokens-dark.css'), renderDarkCss(mod.DARK_TOKENS)],
    [path.join(OUT_DIR, 'tokens-print.css'), renderPrintCss(mod.LIGHT_TOKENS)],
  ];
  for (const [file, content] of files) {
    writeFileSync(file, content, 'utf8');
    console.log(`gen:tokens → ${path.relative(ROOT, file)}`);
  }
}

const isMain = process.argv[1] === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch((error) => {
    console.error(`gen:tokens 失败:${error instanceof Error ? error.message : error}`);
    process.exit(1);
  });
}
