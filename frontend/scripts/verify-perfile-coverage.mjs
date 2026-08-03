#!/usr/bin/env node
/**
 * 新增/变更源码逐文件覆盖率门禁。
 *
 * `vitest` 的全局阈值会让高覆盖文件掩盖低覆盖文件。这里直接读取当前
 * 分支相对基线新增/变更的全部前端 TS/TSX 源码，再逐文件校验
 * lines/functions/branches/statements 四项均不低于 90%。测试、声明、纯测试
 * 工具等与 Vitest coverage.exclude 同义的文件不计入源码门禁。
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const THRESHOLD = 90;
export const METRICS = ['lines', 'functions', 'branches', 'statements'];

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(ROOT, '..');
const SUMMARY_PATH = resolve(ROOT, 'coverage/coverage-summary.json');

export function isGatedSource(file) {
  return (
    /^frontend\/src\/.*\.tsx?$/.test(file) &&
    !file.includes('/__tests__/') &&
    !/\.(?:test|spec)\.tsx?$/.test(file) &&
    !/\.d\.ts$/.test(file) &&
    file !== 'frontend/src/main.tsx' &&
    !file.startsWith('frontend/src/types/') &&
    !file.startsWith('frontend/src/test-utils/')
  );
}

function git(args) {
  return execFileSync('git', args, {
    cwd: REPO,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

function outputLines(output) {
  return output.split('\n').filter(Boolean);
}

export function changedSources(base, runGit = git) {
  const mergeBase = runGit(['merge-base', base, 'HEAD']);
  if (!mergeBase) throw new Error(`无法确定 ${base} 与 HEAD 的 merge-base`);

  // CI 中所有变更都已提交，第一项足够；本地开发时还必须看到 index、worktree
  // 和 untracked 源码，否则新文件会在提交前静默绕过门禁。
  const outputs = [
    runGit([
      'diff',
      '--name-only',
      '--diff-filter=ACMR',
      `${mergeBase}...HEAD`,
      '--',
      'frontend/src',
    ]),
    runGit(['diff', '--cached', '--name-only', '--diff-filter=ACMR', '--', 'frontend/src']),
    runGit(['diff', '--name-only', '--diff-filter=ACMR', '--', 'frontend/src']),
    runGit(['ls-files', '--others', '--exclude-standard', '--', 'frontend/src']),
  ];

  return [...new Set(outputs.flatMap(outputLines))].filter(isGatedSource).sort();
}

export function evaluateCoverage(files, summary) {
  return files.map((repoFile) => {
    const frontendFile = repoFile.slice('frontend/'.length);
    const absoluteFile = resolve(ROOT, frontendFile);
    const data = summary[absoluteFile];
    if (!data) return { file: frontendFile, missing: true, failed: ['coverage=missing'] };

    const failed = METRICS.filter((metric) => {
      const value = data[metric]?.pct;
      // V8 对没有可执行项的指标报告 Unknown；该指标没有未覆盖项。
      return value !== 'Unknown' && (typeof value !== 'number' || value < THRESHOLD);
    }).map((metric) => `${metric}=${data[metric]?.pct ?? 'missing'}%`);
    return { file: frontendFile, data, missing: false, failed };
  });
}

function argValue(name, fallback) {
  const index = process.argv.indexOf(name);
  return index !== -1 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function main() {
  if (!existsSync(SUMMARY_PATH)) {
    console.error(
      `ERROR: 缺少 ${SUMMARY_PATH} —— 请先运行 \`npx vitest run --coverage\` 生成覆盖率数据。`,
    );
    process.exit(1);
  }

  const defaultBase = process.env.GITHUB_BASE_REF
    ? `origin/${process.env.GITHUB_BASE_REF}`
    : 'origin/main';
  const base = argValue('--base', defaultBase);
  let files;
  try {
    files = changedSources(base);
  } catch (error) {
    console.error(`ERROR: 无法读取相对 ${base} 的变更源码: ${error.message}`);
    process.exit(1);
  }

  const summary = JSON.parse(readFileSync(SUMMARY_PATH, 'utf8'));
  const results = evaluateCoverage(files, summary);
  const failures = results.filter((result) => result.failed.length > 0);

  console.log(
    `per-file 变更源码门禁:相对 ${base} 扫描 ${results.length} 个 TS/TSX 源文件,` +
      `阈值 ${THRESHOLD}%(lines/functions/branches/statements)。`,
  );

  if (failures.length > 0) {
    console.error(`ERROR: ${failures.length} 个变更源码文件未达 per-file ${THRESHOLD}% 门禁:`);
    for (const result of failures) {
      console.error(`  - ${result.file}  [${result.failed.join(', ')}]`);
    }
    process.exit(1);
  }

  console.log('OK 变更源码逐文件覆盖率门禁通过。');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
