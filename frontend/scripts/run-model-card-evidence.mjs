#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { collectRuntimeEvidenceClaims, comparePngBuffers } from './model-card-lib.mjs';

const MODULE_PATH = fileURLToPath(import.meta.url);
const FRONTEND_ROOT = resolve(dirname(MODULE_PATH), '..');
const CARD_PATH = resolve(FRONTEND_ROOT, 'model-card/mes108-react-migration.json');

export function parseEvidenceArguments(argv) {
  let mode;
  let output;
  let repository;
  let headSha;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (['--mode', '--output', '--repository', '--head'].includes(argument)) {
      const value = argv[index + 1];
      if (typeof value !== 'string' || value.length === 0 || value.startsWith('--')) {
        throw new Error(`${argument} requires a value`);
      }
      if (argument === '--mode') mode = value;
      if (argument === '--output') output = value;
      if (argument === '--repository') repository = value;
      if (argument === '--head') headSha = value;
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }
  if (!['plan', 'run'].includes(mode)) throw new Error('--mode must be plan or run');
  if (mode === 'run') {
    if (output === undefined) throw new Error('run mode requires --output');
    if (!/^[^/]+\/[^/]+$/u.test(repository ?? '')) {
      throw new Error('run mode requires --repository owner/name');
    }
    if (!/^[0-9a-f]{40}$/u.test(headSha ?? '')) {
      throw new Error('run mode requires --head as a full lowercase commit SHA');
    }
  }
  return {
    mode,
    ...(output === undefined ? {} : { output }),
    ...(repository === undefined ? {} : { repository }),
    ...(headSha === undefined ? {} : { headSha }),
  };
}

function safeTarget(frontendRoot, path, label) {
  if (typeof path !== 'string' || isAbsolute(path) || path.split(/[\\/]/u).includes('..')) {
    throw new Error(`${label} must stay inside frontend root`);
  }
  const target = resolve(frontendRoot, path);
  if (!existsSync(target)) {
    throw new Error(`${label} does not resolve: ${String(path)}`);
  }
  return target;
}

function escapeRegularExpression(value) {
  return String(value).replaceAll(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

export function defaultPlaywrightRun(run, { frontendRoot, execute = spawnSync } = {}) {
  safeTarget(frontendRoot, run.config, 'Playwright config');
  safeTarget(frontendRoot, run.spec, 'Playwright spec');
  const executable = safeTarget(
    frontendRoot,
    'node_modules/.bin/playwright',
    'Playwright executable',
  );
  const reporter = safeTarget(
    frontendRoot,
    'scripts/mes108-playwright-reporter.mjs',
    'Playwright reporter',
  );
  const reportDirectory = mkdtempSync(resolve(tmpdir(), 'mes108-playwright-'));
  const reportPath = resolve(reportDirectory, 'report.json');
  const result = execute(
    executable,
    [
      'test',
      '--config',
      run.config,
      '--project',
      run.project,
      '--grep',
      `${escapeRegularExpression(run.testTitle)}$`,
      '--reporter',
      reporter,
      run.spec,
    ],
    {
      cwd: frontendRoot,
      encoding: 'utf8',
      env: { ...process.env, MES108_PLAYWRIGHT_REPORT: reportPath },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  if (result.error !== undefined || result.status !== 0) {
    const detail = String(result.stderr ?? result.error ?? '')
      .trim()
      .slice(-4000);
    throw new Error(
      `Playwright evidence test failed: ${run.spec} :: ${run.testTitle}${detail ? ` (${detail})` : ''}`,
    );
  }
  if (!existsSync(reportPath))
    throw new Error('Playwright evidence reporter did not produce output');
  return JSON.parse(readFileSync(reportPath, 'utf8'));
}

function groupClaims(claims) {
  const groups = new Map();
  for (const claim of claims) {
    const key = JSON.stringify([claim.config, claim.project, claim.spec, claim.testTitle]);
    const group = groups.get(key) ?? {
      config: claim.config,
      project: claim.project,
      spec: claim.spec,
      testTitle: claim.testTitle,
      claims: [],
    };
    group.claims.push(claim);
    groups.set(key, group);
  }
  return [...groups.values()];
}

function matchingTest(report, run) {
  if (report?.schemaVersion !== 1 || report.status !== 'passed' || !Array.isArray(report.tests)) {
    throw new Error('Playwright evidence report is malformed or unsuccessful');
  }
  const matches = report.tests.filter(
    (test) =>
      test?.spec === run.spec && test?.testTitle === run.testTitle && test?.project === run.project,
  );
  if (report.tests.length !== 1 || matches.length !== 1)
    throw new Error('Playwright evidence report must contain one exact test');
  const match = matches[0];
  if (
    match.status !== 'passed' ||
    match.expectedStatus !== 'passed' ||
    !Array.isArray(match.apiSteps) ||
    !Array.isArray(match.screenshotOutputs)
  ) {
    throw new Error('Playwright evidence test was skipped, fixed, or unsuccessful');
  }
  return match;
}

function modesFromApiSteps(apiSteps) {
  const source = Array.isArray(apiSteps) ? apiSteps.join('\n') : '';
  return [
    ['mouse', /(?:click|dblclick|dragTo|hover|mouse\.(?:down|move|up))/u],
    ['keyboard', /(?:keyboard\.|\.press|\.pressSequentially|\.type)/u],
    ['touch', /(?:touchscreen\.tap|\.tap)/u],
  ]
    .filter(([, pattern]) => pattern.test(source))
    .map(([mode]) => mode);
}

function validateRuntimeEnvironment(card, test, run) {
  const expectedViewport = {
    phone: { width: 390, height: 844 },
    wide: { width: 1440, height: 900 },
  }[run.project];
  const expected = {
    browserName: String(card.visualEnvironment?.browser ?? '').toLowerCase(),
    locale: card.visualEnvironment?.locale,
    timezoneId: card.visualEnvironment?.timezone,
    deviceScaleFactor: card.visualEnvironment?.deviceScaleFactor,
    viewport: expectedViewport,
  };
  for (const [field, value] of Object.entries(expected)) {
    if (JSON.stringify(test.environment?.[field]) !== JSON.stringify(value)) {
      throw new Error(`Playwright evidence environment ${field} does not match the model card`);
    }
  }
  const resolvedViewport = `${expectedViewport?.width}x${expectedViewport?.height}`;
  for (const claim of run.claims.filter((candidate) => candidate.kind === 'visual')) {
    if (claim.viewport !== resolvedViewport) {
      throw new Error('Playwright evidence project viewport does not match the visual claim');
    }
  }
}

function isolateVisualArtifacts(run, frontendRoot) {
  const originals = new Map();
  const targets = new Map();
  for (const claim of run.claims.filter((candidate) => candidate.kind === 'visual')) {
    const target = safeTarget(frontendRoot, claim.path, 'visual artifact');
    originals.set(target, readFileSync(target));
    targets.set(claim.key, target);
    unlinkSync(target);
  }
  return {
    targets,
    restore() {
      for (const [target, content] of originals) {
        writeFileSync(target, content);
      }
    },
  };
}

export function runModelCardEvidence(
  card,
  {
    frontendRoot = FRONTEND_ROOT,
    repository,
    headSha,
    modelCardSha256,
    now = () => new Date(),
    runPlaywright = (run) => defaultPlaywrightRun(run, { frontendRoot }),
  },
) {
  const claims = collectRuntimeEvidenceClaims(card);
  const results = [];
  for (const run of groupClaims(claims)) {
    const isolated = isolateVisualArtifacts(run, frontendRoot);
    try {
      const test = matchingTest(runPlaywright(run), run);
      validateRuntimeEnvironment(card, test, run);
      const executedInputModes = modesFromApiSteps(test.apiSteps);
      const screenshotCompared = test.apiSteps.some((step) => /toHaveScreenshot/u.test(step));
      const screenshotCalled = test.apiSteps.some((step) => /(?:^|\.)screenshot/u.test(step));
      const expectedScreenshotPaths = new Set(
        run.claims.filter((claim) => claim.kind === 'visual').map((claim) => claim.path),
      );
      const reportedScreenshotPaths = new Set(test.screenshotOutputs.map((output) => output?.path));
      if (
        test.screenshotOutputs.length !== expectedScreenshotPaths.size ||
        reportedScreenshotPaths.size !== expectedScreenshotPaths.size ||
        [...reportedScreenshotPaths].some((path) => !expectedScreenshotPaths.has(path))
      ) {
        throw new Error('Playwright screenshot outputs must exactly match the visual claims');
      }
      for (const claim of run.claims) {
        if (claim.kind === 'interaction') {
          results.push({
            key: claim.key,
            status: 'passed',
            executedInputModes,
          });
          continue;
        }
        const screenshotOutput = test.screenshotOutputs.find(
          (candidate) => candidate?.path === claim.path,
        );
        if (screenshotOutput === undefined) {
          throw new Error(`Playwright did not report the claimed screenshot path ${claim.path}`);
        }
        const target = isolated.targets.get(claim.key);
        if (target === undefined || !existsSync(target)) {
          throw new Error(`Playwright did not produce visual artifact ${claim.path}`);
        }
        if (!screenshotCompared || !screenshotCalled) {
          throw new Error(
            'Playwright visual test must execute comparison and screenshot API steps',
          );
        }
        const actual = readFileSync(target);
        const actualSha256 = createHash('sha256').update(actual).digest('hex');
        if (screenshotOutput.sha256 !== actualSha256) {
          throw new Error(`Playwright reported screenshot sha256 does not match ${claim.path}`);
        }
        const baselineTarget = safeTarget(
          frontendRoot,
          claim.comparison?.baselinePath,
          'visual baseline',
        );
        const baseline = readFileSync(baselineTarget);
        const comparison = comparePngBuffers(actual, baseline);
        if (comparison === null) throw new Error(`cannot compare visual artifact ${claim.path}`);
        results.push({
          key: claim.key,
          status: 'passed',
          screenshotProduced: true,
          artifactSha256: actualSha256,
          baselineSha256: createHash('sha256').update(baseline).digest('hex'),
          totalPixels: comparison.totalPixels,
          diffPixels: comparison.diffPixels,
        });
      }
    } catch (error) {
      isolated.restore();
      throw error;
    }
  }
  return {
    schemaVersion: 1,
    source: 'github-actions-playwright',
    repository,
    headSha,
    modelCardSha256,
    generatedAt: now().toISOString(),
    results,
  };
}

export function executeEvidenceCli(argv, options = {}) {
  const args = parseEvidenceArguments(argv);
  const frontendRoot = options.frontendRoot ?? FRONTEND_ROOT;
  const cardPath = options.cardPath ?? CARD_PATH;
  const readFile = options.readFile ?? readFileSync;
  const writeFile = options.writeFile ?? writeFileSync;
  const makeDirectory = options.makeDirectory ?? mkdirSync;
  const cardSource = readFile(cardPath, 'utf8');
  const card = JSON.parse(cardSource);
  if (args.mode === 'plan') {
    return {
      exitCode: 0,
      stdout: `${collectRuntimeEvidenceClaims(card).length > 0}\n`,
      stderr: '',
    };
  }
  const evidenceRun = runModelCardEvidence(card, {
    frontendRoot,
    repository: args.repository,
    headSha: args.headSha,
    modelCardSha256: createHash('sha256').update(cardSource).digest('hex'),
    ...(options.runPlaywright === undefined ? {} : { runPlaywright: options.runPlaywright }),
    ...(options.now === undefined ? {} : { now: options.now }),
  });
  const output = resolve(args.output);
  makeDirectory(dirname(output), { recursive: true });
  writeFile(output, `${JSON.stringify(evidenceRun, null, 2)}\n`, 'utf8');
  return { exitCode: 0, stdout: `MES-108 runtime evidence written to ${output}\n`, stderr: '' };
}

export function runEvidenceProcess(
  argv,
  options = {},
  {
    execute = executeEvidenceCli,
    writeStdout = (value) => process.stdout.write(value),
    writeStderr = (value) => process.stderr.write(value),
    setExitCode = (value) => {
      process.exitCode = value;
    },
  } = {},
) {
  try {
    const result = execute(argv, options);
    if (result.stdout) writeStdout(result.stdout);
    if (result.stderr) writeStderr(result.stderr);
    setExitCode(result.exitCode);
    return result;
  } catch (error) {
    const stderr = `${error instanceof Error ? error.message : String(error)}\n`;
    writeStderr(stderr);
    setExitCode(1);
    return { exitCode: 1, stdout: '', stderr };
  }
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === MODULE_PATH) {
  runEvidenceProcess(process.argv.slice(2));
}
