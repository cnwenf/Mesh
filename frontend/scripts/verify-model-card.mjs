#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { renderModelCardMarkdown, validateModelCard } from './model-card-lib.mjs';

const MODULE_PATH = fileURLToPath(import.meta.url);
const FRONTEND_ROOT = resolve(dirname(MODULE_PATH), '..');
const REPOSITORY_ROOT = resolve(FRONTEND_ROOT, '..');
const MANIFEST_PATH = resolve(FRONTEND_ROOT, 'model-card/mes108-react-migration.json');
const DOCUMENT_PATH = resolve(
  REPOSITORY_ROOT,
  'docs/specs/frontend/mes108-reconciliation/react-migration-model-card.md',
);

export function parseArguments(argv) {
  let mode = 'audit';
  let write = false;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--write') {
      write = true;
    } else if (argument === '--mode') {
      mode = argv[index + 1];
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }
  if (!['audit', 'release'].includes(mode)) {
    throw new Error(`--mode must be audit or release, received: ${String(mode)}`);
  }
  return { mode, write };
}

export function readPinnedSource(
  revision,
  path,
  { repositoryRoot = REPOSITORY_ROOT, execute = execFileSync } = {},
) {
  try {
    return execute('git', ['show', `${revision}:${path}`], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (error) {
    const detail =
      error !== null && typeof error === 'object' && 'stderr' in error
        ? String(error.stderr).trim()
        : String(error);
    throw new Error(`cannot read pinned source ${revision}:${path}${detail ? ` (${detail})` : ''}`);
  }
}

export function verifyModelCard(argv, options = {}) {
  const { mode, write } = parseArguments(argv);
  const frontendRoot = options.frontendRoot ?? FRONTEND_ROOT;
  const repositoryRoot = options.repositoryRoot ?? REPOSITORY_ROOT;
  const manifestPath = options.manifestPath ?? MANIFEST_PATH;
  const documentPath = options.documentPath ?? DOCUMENT_PATH;
  const readFile = options.readFile ?? readFileSync;
  const fileExists = options.fileExists ?? existsSync;
  const makeDirectory = options.makeDirectory ?? mkdirSync;
  const writeFile = options.writeFile ?? writeFileSync;
  const validate = options.validate ?? validateModelCard;
  const render = options.render ?? renderModelCardMarkdown;
  const readPinned =
    options.readPinned ??
    ((revision, path) => readPinnedSource(revision, path, { repositoryRoot }));

  const card = JSON.parse(readFile(manifestPath, 'utf8'));
  const revision = card.blueprint?.revision;
  const errors = validate(card, {
    frontendRoot,
    mode,
    prototypeRouteSource: readPinned(revision, 'frontend-prototype/app.js'),
    prototypeTokenSource: readPinned(revision, 'frontend-prototype/styles.css'),
  });
  if (errors.length > 0) {
    return {
      exitCode: 1,
      stdout: '',
      stderr: `MES-108 model card ${mode} validation failed (${errors.length}):\n${errors.map((error) => `- ${error}`).join('\n')}\n`,
    };
  }

  const markdown = render(card);
  if (write) {
    makeDirectory(dirname(documentPath), { recursive: true });
    writeFile(documentPath, markdown, 'utf8');
    return { exitCode: 0, stdout: 'MES-108 model card document updated.\n', stderr: '' };
  }
  if (!fileExists(documentPath)) {
    return {
      exitCode: 1,
      stdout: '',
      stderr: 'MES-108 model card document is missing; run with --write.\n',
    };
  }
  if (readFile(documentPath, 'utf8') !== markdown) {
    return {
      exitCode: 1,
      stdout: '',
      stderr: 'MES-108 model card document has drifted; run with --write.\n',
    };
  }
  return {
    exitCode: 0,
    stdout: `MES-108 model card ${mode} validation passed.\n`,
    stderr: '',
  };
}

export function executeModelCardCli(argv, options = {}) {
  try {
    return verifyModelCard(argv, options);
  } catch (error) {
    return {
      exitCode: 1,
      stdout: '',
      stderr: `${error instanceof Error ? error.message : String(error)}\n`,
    };
  }
}

export function runModelCardProcess(
  argv,
  options = {},
  {
    writeStdout = (value) => process.stdout.write(value),
    writeStderr = (value) => process.stderr.write(value),
    setExitCode = (value) => {
      process.exitCode = value;
    },
  } = {},
) {
  const result = executeModelCardCli(argv, options);
  if (result.stdout) writeStdout(result.stdout);
  if (result.stderr) writeStderr(result.stderr);
  setExitCode(result.exitCode);
  return result;
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === MODULE_PATH) {
  runModelCardProcess(process.argv.slice(2));
}
