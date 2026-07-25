#!/usr/bin/env node
/**
 * 新增/变更代码覆盖率校验(MES-16 验收基线:新增代码覆盖率 ≥90%)。
 *
 * 原理:git diff 出相对 base 分支变更的 src/**.{ts,tsx} 行号,
 * 与 vitest coverage-summary/coverage-final.json 的语句映射取交集,
 * 统计「变更行中被覆盖的比例」,< 90% 即失败。
 *
 * 用法:node scripts/verify-coverage.mjs [--base <ref>]  (默认 base=main;
 *       需先跑 npm run test:coverage 生成 coverage/coverage-final.json)
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const THRESHOLD = 90;
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(ROOT, '..');

function argValue(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx !== -1 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const base = argValue('--base', 'main');

function git(args) {
  try {
    return execFileSync('git', args, { cwd: REPO, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  } catch {
    return '';
  }
}

/** 解析 git diff --unified=0,得到 { 相对仓库路径: Set<新增/变更行号> }(仅 frontend/src 下 ts/tsx) */
function changedLines() {
  const mergeBase = git(['merge-base', base, 'HEAD']).trim() || base;
  const diff = git(['diff', '--unified=0', '--no-color', mergeBase, 'HEAD', '--', 'frontend/src']);
  const result = new Map();
  let file = null;
  for (const line of diff.split('\n')) {
    const fileMatch = /^\+\+\+ b\/(.+)$/.exec(line);
    if (fileMatch) {
      file = fileMatch[1];
      continue;
    }
    const hunkMatch = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(line);
    if (hunkMatch && file && /\.tsx?$/.test(file) && !file.includes('__tests__')) {
      const start = Number(hunkMatch[1]);
      const count = hunkMatch[2] === undefined ? 1 : Number(hunkMatch[2]);
      if (count === 0) continue;
      const lines = result.get(file) ?? new Set();
      for (let n = start; n < start + count; n++) lines.add(n);
      result.set(file, lines);
    }
  }
  return result;
}

function main() {
  const coveragePath = resolve(ROOT, 'coverage/coverage-final.json');
  if (!existsSync(coveragePath)) {
    console.error('[verify-coverage] coverage/coverage-final.json 不存在,请先运行 npm run test:coverage');
    process.exit(2);
  }

  const changed = changedLines();
  if (changed.size === 0) {
    console.log('[verify-coverage] 无 frontend/src 代码变更,跳过新增代码覆盖率校验');
    return;
  }

  const coverage = JSON.parse(readFileSync(coveragePath, 'utf8'));
  let coveredChanged = 0;
  let totalChanged = 0;
  const uncoveredReport = [];

  for (const [file, lines] of changed) {
    const abs = resolve(REPO, file);
    const entry = coverage[abs];
    if (!entry) {
      // 变更文件完全未出现在覆盖率报告中(无测试触及)→ 按未覆盖计入
      totalChanged += lines.size;
      uncoveredReport.push(`${file}: 整个文件无测试覆盖(${lines.size} 行变更)`);
      continue;
    }
    for (const [stmtId, loc] of Object.entries(entry.statementMap)) {
      const hits = entry.s[stmtId];
      for (let ln = loc.start.line; ln <= loc.end.line; ln++) {
        if (lines.has(ln)) {
          totalChanged++;
          if (hits > 0) coveredChanged++;
          else uncoveredReport.push(`${file}:${ln}`);
          lines.delete(ln); // 每行只计一次
        }
      }
    }
  }

  const ratio = totalChanged === 0 ? 100 : (coveredChanged / totalChanged) * 100;
  console.log(
    `[verify-coverage] 变更语句行 ${totalChanged},已覆盖 ${coveredChanged},覆盖率 ${ratio.toFixed(1)}%(门禁 ${THRESHOLD}%)`,
  );
  if (ratio < THRESHOLD) {
    console.error('[verify-coverage] 未覆盖的变更行:');
    for (const line of uncoveredReport.slice(0, 50)) console.error(`  - ${line}`);
    if (uncoveredReport.length > 50) console.error(`  … 另有 ${uncoveredReport.length - 50} 行`);
    process.exit(1);
  }
  console.log('[verify-coverage] PASS');
}

main();
