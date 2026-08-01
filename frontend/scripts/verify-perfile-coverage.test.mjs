import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { changedSources, evaluateCoverage, isGatedSource } from './verify-perfile-coverage.mjs';

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function summaryPath(repoFile) {
  return resolve(FRONTEND_ROOT, repoFile.slice('frontend/'.length));
}

function metrics(lines = 100, functions = 100, branches = 100, statements = 100) {
  return {
    lines: { pct: lines },
    functions: { pct: functions },
    branches: { pct: branches },
    statements: { pct: statements },
  };
}

describe('isGatedSource', () => {
  it('includes runtime TS/TSX anywhere under frontend/src, not only legacy directories', () => {
    expect(isGatedSource('frontend/src/features/new-area/NewPage.tsx')).toBe(true);
    expect(isGatedSource('frontend/src/workspace/featureFlags.tsx')).toBe(true);
    expect(isGatedSource('frontend/src/shell/agentTriggerNotice.ts')).toBe(true);
  });

  it.each([
    'backend/src/worker.ts',
    'frontend/e2e/flow.spec.ts',
    'frontend/src/main.tsx',
    'frontend/src/env.test.ts',
    'frontend/src/foo/view.spec.tsx',
    'frontend/src/foo/__tests__/view.tsx',
    'frontend/src/types/domain.ts',
    'frontend/src/test-utils/render.tsx',
    'frontend/src/vite-env.d.ts',
    'frontend/src/styles.css',
  ])('excludes non-runtime source %s', (file) => {
    expect(isGatedSource(file)).toBe(false);
  });
});

describe('changedSources', () => {
  it('deduplicates committed, staged, unstaged, and untracked runtime source', () => {
    const calls = [];
    const runGit = (args) => {
      calls.push(args);
      if (args[0] === 'merge-base') return 'abc123';
      if (args[0] === 'ls-files') {
        return ['frontend/src/new/Untracked.ts', 'frontend/src/new/Untracked.test.ts'].join('\n');
      }
      if (args.includes('--cached')) {
        return ['frontend/src/new/Staged.tsx', 'frontend/src/new/Shared.ts'].join('\n');
      }
      if (args.includes('abc123...HEAD')) {
        return [
          'frontend/src/new/Committed.ts',
          'frontend/src/new/Shared.ts',
          'frontend/src/new/__tests__/helper.ts',
        ].join('\n');
      }
      return ['frontend/src/new/Dirty.tsx', 'frontend/src/new/Shared.ts'].join('\n');
    };

    expect(changedSources('origin/review', runGit)).toEqual([
      'frontend/src/new/Committed.ts',
      'frontend/src/new/Dirty.tsx',
      'frontend/src/new/Shared.ts',
      'frontend/src/new/Staged.tsx',
      'frontend/src/new/Untracked.ts',
    ]);
    expect(calls[0]).toEqual(['merge-base', 'origin/review', 'HEAD']);
    expect(calls).toHaveLength(5);
  });

  it('fails explicitly when the requested base has no merge-base', () => {
    expect(() => changedSources('origin/missing', () => '')).toThrow(
      '无法确定 origin/missing 与 HEAD 的 merge-base',
    );
  });
});

describe('evaluateCoverage', () => {
  it('evaluates all four metrics independently at the 90% boundary', () => {
    const passing = 'frontend/src/new/Passing.ts';
    const failing = 'frontend/src/new/Failing.tsx';
    const summary = {
      [summaryPath(passing)]: metrics(90, 90, 90, 90),
      [summaryPath(failing)]: metrics(89.99, 89, 88, 87),
    };

    expect(evaluateCoverage([passing, failing], summary)).toEqual([
      expect.objectContaining({ file: 'src/new/Passing.ts', missing: false, failed: [] }),
      expect.objectContaining({
        file: 'src/new/Failing.tsx',
        missing: false,
        failed: ['lines=89.99%', 'functions=89%', 'branches=88%', 'statements=87%'],
      }),
    ]);
  });

  it('fails missing files but accepts Unknown for an empty metric', () => {
    const emptyMetric = 'frontend/src/new/TypesOnly.ts';
    const missing = 'frontend/src/new/Missing.ts';
    const summary = {
      [summaryPath(emptyMetric)]: metrics('Unknown', 'Unknown', 'Unknown', 'Unknown'),
    };

    expect(evaluateCoverage([emptyMetric, missing], summary)).toEqual([
      expect.objectContaining({ file: 'src/new/TypesOnly.ts', missing: false, failed: [] }),
      {
        file: 'src/new/Missing.ts',
        missing: true,
        failed: ['coverage=missing'],
      },
    ]);
  });
});
