#!/usr/bin/env node
/**
 * design-quality §10.2 的结构级无障碍门禁。
 *
 * 运行时语义由 Playwright + axe 验证；本脚本把最容易回归、且可静态判定的两条
 * 契约变成 fail-closed 检查：业务层不得自造模态框，原生表格必须有 caption，
 * 每个 th 必须声明 scope，页面主地标只能由三类外壳/兜底页拥有。
 */
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'src');
const DIALOG_OWNERS = new Set([
  'src/design/components/Dialog.tsx',
  'src/design/components/Drawer.tsx',
  'src/design/components/Popover.tsx',
]);
const MAIN_OWNERS = new Set([
  'src/design/components/PublicFlowShell.tsx',
  'src/features/approvals/ApprovalsPage.tsx',
  'src/shell/AppShell.tsx',
  'src/shell/pages/ErrorPage.tsx',
  'src/shell/pages/NotFoundPage.tsx',
]);

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(absolute));
    else if (entry.isFile() && entry.name.endsWith('.tsx')) files.push(absolute);
  }
  return files;
}

function lineNumber(source, index) {
  return source.slice(0, index).split('\n').length;
}

export function findA11yContractViolations() {
  const violations = [];
  for (const file of walk(SRC)) {
    const relative = path.relative(ROOT, file);
    if (relative.includes(`${path.sep}__tests__${path.sep}`)) continue;
    const source = readFileSync(file, 'utf8');

    if (!DIALOG_OWNERS.has(relative)) {
      for (const match of source.matchAll(/role=(?:"dialog"|'dialog'|\{"dialog"\})/g)) {
        violations.push(
          `${relative}:${lineNumber(source, match.index ?? 0)} owns role=dialog outside shared Dialog/Drawer/Popover`,
        );
      }
    }

    if (!MAIN_OWNERS.has(relative)) {
      for (const match of source.matchAll(/<main\b/g)) {
        violations.push(
          `${relative}:${lineNumber(source, match.index ?? 0)} owns <main> outside an approved page shell`,
        );
      }
    }

    for (const tableMatch of source.matchAll(/<table\b[\s\S]*?<\/table>/g)) {
      const table = tableMatch[0];
      const tableLine = lineNumber(source, tableMatch.index ?? 0);
      if (!/<caption\b/.test(table)) {
        violations.push(`${relative}:${tableLine} table is missing a caption`);
      }
      for (const headingMatch of table.matchAll(/<th\b[^>]*>/g)) {
        if (!/\bscope=/.test(headingMatch[0])) {
          const offset = (tableMatch.index ?? 0) + (headingMatch.index ?? 0);
          violations.push(`${relative}:${lineNumber(source, offset)} th is missing scope`);
        }
      }
    }
  }
  return violations;
}

function main() {
  const violations = findA11yContractViolations();
  if (violations.length > 0) {
    console.error(`a11y contract failed (${violations.length})`);
    for (const violation of violations) console.error(`- ${violation}`);
    process.exit(1);
  }
  console.log('a11y contract passed: page landmarks + shared overlays + semantic tables');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
