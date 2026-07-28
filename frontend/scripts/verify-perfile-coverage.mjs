#!/usr/bin/env node
/**
 * 目录级 per-file 覆盖率门禁(真实执行版,替代 vite.config 里被静默忽略的
 * glob 键 thresholds)。
 *
 * 背景:vitest `coverage.thresholds` 只识别全局数值键与 `perFile`/`100`
 * 两个布尔;以 glob 字符串为键的「目录级阈值」属未知属性,会被静默忽略
 * (labels/auth 先例起的历史误配,目录门禁从未真实执行)。全局 `perFile: true`
 * 会一刀切全仓、与存量模块不兼容,故以本脚本对指定目录逐文件强制阈值:
 * 读取 `coverage/coverage-summary.json`(先跑 `vitest run --coverage`),
 * PER_FILE_DIRS 内任一文件任一指标低于 THRESHOLD 即非零退出并逐文件列明,
 * 接入 `test:coverage` 后在本地与 CI 真实生效。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const THRESHOLD = 90;
const METRICS = ['lines', 'functions', 'branches', 'statements'];

/**
 * 目录级 per-file 门禁名单:目录内每个源文件逐文件 ≥90,不被全局均值掩盖。
 * labels/auth 的同类先例为历史误配遗留的 per-file 缺口,随各自模块清偿后
 * 纳入本名单(见 MES-60 第 2 轮验收 R1)。
 */
const PER_FILE_DIRS = [
  'src/features/agents/',
  'src/features/onboarding/',
  'src/features/members/',
  'src/features/comments/',
  'src/features/inbox/',
  'src/features/runtimes/',
  'src/features/autopilots/',
  'src/features/chat/',
  'src/features/analytics/',
];

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SUMMARY_PATH = resolve(ROOT, 'coverage/coverage-summary.json');

if (!existsSync(SUMMARY_PATH)) {
  console.error(
    `ERROR: 缺少 ${SUMMARY_PATH} —— 请先运行 \`npx vitest run --coverage\` 生成覆盖率数据。`,
  );
  process.exit(1);
}

const summary = JSON.parse(readFileSync(SUMMARY_PATH, 'utf8'));
const failures = [];
let gated = 0;

for (const [file, data] of Object.entries(summary)) {
  if (file === 'total' || file.includes('__tests__')) continue;
  const rel = file.includes('/src/') ? `src/${file.split('/src/').slice(1).join('/src/')}` : '';
  if (!PER_FILE_DIRS.some((dir) => rel.startsWith(dir))) continue;
  gated += 1;
  const failed = METRICS.filter((m) => data[m].pct < THRESHOLD).map(
    (m) => `${m}=${data[m].pct}%`,
  );
  if (failed.length > 0) failures.push(`${rel}  [${failed.join(', ')}]`);
}

console.log(
  `per-file 目录门禁:扫描 ${PER_FILE_DIRS.join(' + ')} 共 ${gated} 个源文件,阈值 ${THRESHOLD}%(lines/functions/branches/statements)。`,
);

if (failures.length > 0) {
  console.error(`ERROR: ${failures.length} 个文件未达 per-file ${THRESHOLD}% 门禁:`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}

console.log('OK 目录级 per-file 覆盖率门禁通过。');
