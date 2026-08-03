/**
 * MES-128 legacy token debt guard.
 * Existing aliases stay for one release cycle, but no file/token pair may grow and no new pair
 * may appear. Deletions are always allowed. `--snapshot` prints the exact current baseline.
 */
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'src');
const BASELINE_PATH = path.join(ROOT, 'scripts', 'legacy-token-baseline.json');
const EXCLUDED = new Set([
  'src/design/tokenValues.ts',
  'src/design/tokens.css',
  'src/design/tokens-dark.css',
  'src/design/tokens-print.css',
]);
const LEGACY_TOKENS = [
  '--color-primary',
  '--color-primary-contrast',
  '--color-primary-bg',
  '--color-danger',
  '--color-danger-contrast',
  '--color-warn',
  '--color-warn-contrast',
  '--color-warn-bg',
  '--color-success',
  '--color-success-contrast',
  '--color-info',
  '--color-info-contrast',
  '--space-5',
  '--space-6',
];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(absolute)));
    else if (/\.(?:css|ts|tsx)$/.test(entry.name)) files.push(absolute);
  }
  return files;
}

const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function snapshot() {
  const result = {};
  for (const absolute of await walk(SRC)) {
    const relative = path.relative(ROOT, absolute).split(path.sep).join('/');
    if (EXCLUDED.has(relative) || relative.includes('/__tests__/')) continue;
    const source = await readFile(absolute, 'utf8');
    for (const token of LEGACY_TOKENS) {
      const count =
        source.match(new RegExp(`var\\(\\s*${escapeRegex(token)}(?=[\\s,)])`, 'g'))?.length ?? 0;
      if (count > 0) result[`${relative}|${token}`] = count;
    }
  }
  return Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
}

const current = await snapshot();
if (process.argv.includes('--snapshot')) {
  process.stdout.write(`${JSON.stringify(current, null, 2)}\n`);
  process.exit(0);
}

let baseline;
try {
  baseline = JSON.parse(await readFile(BASELINE_PATH, 'utf8'));
} catch (error) {
  console.error(
    `legacy token baseline is unavailable: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(1);
}

const increases = Object.entries(current).filter(([key, count]) => count > (baseline[key] ?? 0));
if (increases.length > 0) {
  console.error(`legacy token debt increased (${increases.length})`);
  for (const [key, count] of increases)
    console.error(`- ${key}: ${baseline[key] ?? 0} -> ${count}`);
  process.exit(1);
}

const totals = {};
for (const [key, count] of Object.entries(current)) {
  const token = key.slice(key.lastIndexOf('|') + 1);
  totals[token] = (totals[token] ?? 0) + count;
}
const total = Object.values(current).reduce((sum, count) => sum + count, 0);
console.log(
  `legacy token debt guard passed: ${total} uses across ${Object.keys(current).length} file/token pairs`,
);
for (const [token, count] of Object.entries(totals).sort()) console.log(`- ${token}: ${count}`);
