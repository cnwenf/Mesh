import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

import {
  evaluateDebt,
  frontendChangedPaths,
  hasFormatViolations,
  parseDebtBaseline,
  parsePathLines,
} from './check-format-lib.mjs';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(scriptDirectory, '..');
const repositoryRoot = resolve(frontendRoot, '..');
const defaultBaseline = resolve(scriptDirectory, 'prettier-debt-baseline.txt');
const prettierBin = resolve(frontendRoot, 'node_modules', '.bin', 'prettier');

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index === -1) return fallback;
  const value = process.argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`);
  return value;
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  return result;
}

function git(args, acceptedStatuses = [0]) {
  const result = run('git', args, repositoryRoot);
  if (!acceptedStatuses.includes(result.status)) {
    throw new Error(result.stderr.trim() || `git ${args.join(' ')} failed`);
  }
  return result.stdout;
}

function prettierList(args) {
  const result = run(prettierBin, ['--list-different', '--ignore-unknown', ...args], frontendRoot);
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(result.stderr.trim() || 'Prettier failed to inspect formatting');
  }
  return parsePathLines(result.stdout);
}

function changedPaths(base) {
  git(['rev-parse', '--verify', '--quiet', `${base}^{commit}`]);
  const outputs = [
    git(['diff', '--name-only', '--diff-filter=ACMR', `${base}...HEAD`]),
    git(['diff', '--name-only', '--diff-filter=ACMR']),
    git(['diff', '--cached', '--name-only', '--diff-filter=ACMR']),
    git(['ls-files', '--others', '--exclude-standard']),
  ];
  return frontendChangedPaths(outputs.flatMap(parsePathLines));
}

function presentPrettierCandidates(paths) {
  return paths
    .map((path) => resolve(repositoryRoot, path))
    .filter((path) => existsSync(path) && statSync(path).isFile())
    .map((path) => relative(frontendRoot, path).replaceAll('\\', '/'));
}

function report(label, paths) {
  if (paths.length === 0) return;
  process.stderr.write(`${label}:\n${paths.map((path) => `  - ${path}`).join('\n')}\n`);
}

try {
  if (!existsSync(prettierBin)) {
    throw new Error('Prettier is not installed; run npm ci first');
  }

  const base = option('--base', process.env.FORMAT_BASE_SHA || 'origin/main');
  const baselinePath = resolve(option('--baseline', defaultBaseline));
  const baseline = parseDebtBaseline(readFileSync(baselinePath, 'utf8'));
  const currentDrift = prettierList(['.']);
  const { newDebt, clearedDebt } = evaluateDebt({ baseline, currentDrift });
  const touched = presentPrettierCandidates(changedPaths(base));
  const touchedDrift = touched.length === 0 ? [] : prettierList(touched);

  report('New formatting debt is not allowed', newDebt);
  report('Remove cleared paths from the formatting debt baseline', clearedDebt);
  report(
    'Changed files must be formatted even when listed in the historical baseline',
    touchedDrift,
  );

  if (hasFormatViolations({ newDebt, clearedDebt, touchedDrift })) {
    process.exitCode = 1;
  } else {
    process.stdout.write(`Formatting gate passed (${baseline.size} historical debt paths).\n`);
  }
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`Formatting gate configuration error: ${message}\n`);
  process.exitCode = 2;
}
