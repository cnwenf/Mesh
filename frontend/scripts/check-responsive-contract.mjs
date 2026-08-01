#!/usr/bin/env node
/**
 * design-quality §8 响应式静态门禁。
 *
 * CSS media query 无法直接消费 TS 常量，因此从 tokenValues.ts 的唯一事实源加载
 * 允许边界，再扫描 src 下所有 CSS。任何自造近似 viewport 宽度都 fail closed；
 * container query 描述组件内在尺寸，不与 viewport 模式边界耦合。
 */
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadTokenValues } from './gen-tokens.mjs';
import { findDisallowedViewportWidths } from './responsive-contract-parser.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(absolute));
    else if (entry.isFile() && entry.name.endsWith('.css')) files.push(absolute);
  }
  return files;
}

function lineNumber(source, index) {
  return source.slice(0, index).split('\n').length;
}

export async function findResponsiveContractViolations() {
  const { VIEWPORT_BREAKPOINTS } = await loadTokenValues();
  const allowed = new Set([
    VIEWPORT_BREAKPOINTS.compact.max,
    VIEWPORT_BREAKPOINTS.medium.min,
    VIEWPORT_BREAKPOINTS.medium.max,
    VIEWPORT_BREAKPOINTS.wide.min,
    VIEWPORT_BREAKPOINTS.wide.max,
    VIEWPORT_BREAKPOINTS.xwide.min,
  ]);
  const violations = [];
  for (const file of walk(path.join(ROOT, 'src'))) {
    const source = readFileSync(file, 'utf8');
    for (const violation of findDisallowedViewportWidths(source, allowed)) {
      violations.push(
        `${path.relative(ROOT, file)}:${lineNumber(source, violation.index)} uses ${violation.value}px; ` +
          'allowed viewport boundaries are 599/600/1023/1024/1439/1440',
      );
    }
  }

  const html = readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  if (!/name="viewport"[^>]*content="[^"]*viewport-fit=cover/.test(html)) {
    violations.push(
      'index.html viewport meta must include viewport-fit=cover for safe-area insets',
    );
  }
  return violations;
}

async function main() {
  const violations = await findResponsiveContractViolations();
  if (violations.length > 0) {
    console.error(`responsive contract failed (${violations.length})`);
    for (const violation of violations) console.error(`- ${violation}`);
    process.exit(1);
  }
  console.log('responsive contract passed: centralized breakpoints + safe-area viewport');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
