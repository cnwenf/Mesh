#!/usr/bin/env node
/**
 * 对比度独立关卡(theme.md §5.4;CI job `contrast` 与本地 `npm run check:contrast`)。
 *
 * 从单一事实源(tokenValues.ts 的 LIGHT_TOKENS/DARK_TOKENS + AA_CONTRAST_PAIRS,
 * contrast.ts 的对比度公式)导入,对亮/暗两套逐对校验:
 * - text 组 ≥ 4.5:1;large-text(大文本)/ graphic(图形元件)组 ≥ 3:1;
 * - 含 alpha 的值(scrim/shadow 级)作 bg 时先对 `--color-bg` 合成再取亮度;
 * - 任一不达标 → 退出码 1。
 *
 * 输出:逐对 `fg on bg ratio threshold PASS/FAIL`,大文本组单列标题;
 * 摘要 `PASS <n> pairs (text <a> / large-text <b> / graphic <c>) × 2 themes`。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { transpileImport } from './gen-tokens.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TOKENS_SOURCE = path.join(ROOT, 'src/design/tokenValues.ts');
const CONTRAST_SOURCE = path.join(ROOT, 'src/design/contrast.ts');

const THEME_SOURCES = [
  ['light', 'LIGHT_TOKENS'],
  ['dark', 'DARK_TOKENS'],
];

const KIND_LABELS = {
  text: 'text 组(阈值 4.5:1)',
  'large-text': 'large-text 组(大文本,阈值 3:1)',
  graphic: 'graphic 组(图形元件,阈值 3:1)',
};

/** 载入 token 源与对比度公式(打包为单个 ESM 后动态 import)。 */
async function loadDesignModules() {
  const stdin = {
    contents: [
      `export * from ${JSON.stringify(TOKENS_SOURCE)};`,
      `export * from ${JSON.stringify(CONTRAST_SOURCE)};`,
    ].join('\n'),
    resolveDir: ROOT,
    loader: 'ts',
  };
  return transpileImport({ bundle: true, stdin });
}

/** bg 含 alpha 时对页面底色合成,返回参与比对的不透明颜色值。 */
function effectiveBg(bgValue, pageBgValue, { parseColor, compositeOver }) {
  const parsed = parseColor(bgValue);
  if (parsed === null || parsed.a >= 1) {
    return bgValue;
  }
  const { r, g, b } = compositeOver(parsed, pageBgValue);
  const toHex = (channel) => channel.toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/**
 * 校验一套主题的全部配对。
 * @returns {{ kind: string; lines: string[]; failCount: number }} 按 kind 分组的逐对报告。
 */
export function checkThemePairs(themeName, tokens, pairs, contrast) {
  const { contrastRatio, WCAG_AA_RATIO, WCAG_AA_LARGE_RATIO } = contrast;
  const pageBg = tokens['--color-bg'];
  const groups = new Map();
  let failCount = 0;

  for (const pair of pairs) {
    const kind = pair.kind ?? 'text';
    const threshold = kind === 'text' ? WCAG_AA_RATIO : WCAG_AA_LARGE_RATIO;
    const fg = tokens[pair.fg];
    const bg = effectiveBg(tokens[pair.bg], pageBg, contrast);
    const ratio = contrastRatio(fg, bg);
    const passed = ratio >= threshold;
    if (!passed) failCount += 1;
    const line = `${passed ? 'PASS' : 'FAIL'} [${themeName}] ${pair.fg} on ${pair.bg}  ${ratio.toFixed(2)} >= ${threshold}`;
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind).push(line);
  }

  return { groups, failCount };
}

async function main() {
  const mod = await loadDesignModules();
  const pairs = mod.AA_CONTRAST_PAIRS;
  const contrast = {
    contrastRatio: mod.contrastRatio,
    parseColor: mod.parseColor,
    compositeOver: mod.compositeOver,
    WCAG_AA_RATIO: mod.WCAG_AA_RATIO,
    WCAG_AA_LARGE_RATIO: mod.WCAG_AA_LARGE_RATIO,
  };

  console.log('check:contrast — WCAG 2.1 AA 逐对校验(亮/暗两套,事实源 tokenValues.ts)');
  let totalFails = 0;
  const aggregated = new Map();
  const counts = { text: 0, 'large-text': 0, graphic: 0 };

  for (const [themeName, exportName] of THEME_SOURCES) {
    const { groups, failCount } = checkThemePairs(themeName, mod[exportName], pairs, contrast);
    totalFails += failCount;
    for (const [kind, lines] of groups) {
      if (!aggregated.has(kind)) aggregated.set(kind, []);
      aggregated.get(kind).push(...lines);
    }
  }

  for (const kind of ['text', 'large-text', 'graphic']) {
    const lines = aggregated.get(kind);
    if (!lines) continue;
    counts[kind] = lines.length / THEME_SOURCES.length;
    console.log(`\n— ${KIND_LABELS[kind]} —`);
    for (const line of lines) console.log(`  ${line}`);
  }

  const pairCount = pairs.length;
  if (totalFails > 0) {
    console.error(
      `\nFAIL ${totalFails} checks failed across ${pairCount} pairs (text ${counts.text} / large-text ${counts['large-text']} / graphic ${counts.graphic}) × ${THEME_SOURCES.length} themes`,
    );
    process.exit(1);
  }
  console.log(
    `\nPASS ${pairCount} pairs (text ${counts.text} / large-text ${counts['large-text']} / graphic ${counts.graphic}) × ${THEME_SOURCES.length} themes`,
  );
}

const isMain = process.argv[1] === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch((error) => {
    console.error(`check:contrast 失败:${error instanceof Error ? error.message : error}`);
    process.exit(1);
  });
}
