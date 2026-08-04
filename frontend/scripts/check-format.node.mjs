import assert from 'node:assert/strict';
import test from 'node:test';

import {
  evaluateDebt,
  frontendChangedPaths,
  hasFormatViolations,
  normalizeChangedPaths,
  parseDebtBaseline,
  parsePathLines,
} from './check-format-lib.mjs';

test('parseDebtBaseline accepts comments and a sorted unique path list', () => {
  const baseline = parseDebtBaseline('# frozen at audit time\na.ts\ndir/b.ts\n');
  assert.deepEqual([...baseline], ['a.ts', 'dir/b.ts']);
});

test('parseDebtBaseline rejects duplicate, unsorted, or absolute entries', () => {
  assert.throws(() => parseDebtBaseline('b.ts\na.ts\n'), /sorted/u);
  assert.throws(() => parseDebtBaseline('a.ts\na.ts\n'), /duplicate/u);
  assert.throws(() => parseDebtBaseline('/a.ts\n'), /relative POSIX/u);
});

test('evaluateDebt reports new drift and cleared baseline entries independently', () => {
  const baseline = new Set(['legacy-a.ts', 'legacy-b.ts']);
  assert.deepEqual(evaluateDebt({ baseline, currentDrift: ['legacy-b.ts', 'new.ts'] }), {
    newDebt: ['new.ts'],
    clearedDebt: ['legacy-a.ts'],
  });
});

test('path normalization is deterministic and rejects paths outside the repository', () => {
  assert.deepEqual(normalizeChangedPaths(['b.ts', 'a.ts', 'b.ts', '../escape.ts', '/abs.ts']), [
    'a.ts',
    'b.ts',
  ]);
  assert.deepEqual(parsePathLines('b.ts\r\na.ts\n'), ['a.ts', 'b.ts']);
});

test('frontendChangedPaths keeps the gate inside the workflow-owned tree', () => {
  assert.deepEqual(
    frontendChangedPaths(['README.md', 'docs/spec.md', 'frontend/src/b.ts', 'frontend/src/a.ts']),
    ['frontend/src/a.ts', 'frontend/src/b.ts'],
  );
});

test('the gate fails closed for each debt class', () => {
  expectClean({ newDebt: [], clearedDebt: [], touchedDrift: [] }, false);
  expectClean({ newDebt: ['new.ts'], clearedDebt: [], touchedDrift: [] }, true);
  expectClean({ newDebt: [], clearedDebt: ['fixed.ts'], touchedDrift: [] }, true);
  expectClean({ newDebt: [], clearedDebt: [], touchedDrift: ['touched.ts'] }, true);
});

function expectClean(result, expected) {
  assert.equal(hasFormatViolations(result), expected);
}
