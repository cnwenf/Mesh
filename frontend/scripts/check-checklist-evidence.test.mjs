import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { test } from 'vitest';
import {
  CHECKLIST_ROWS,
  CHECKLIST_VARIANTS,
  EXPECTED_CHECKLIST_CELLS,
} from './checklist-evidence-contract.mjs';
import {
  ChecklistEvidenceError,
  pngDimensions,
  sha256,
  validateChecklistEvidence,
} from './check-checklist-evidence-lib.mjs';

function fakePng(width, height, salt) {
  const buffer = Buffer.alloc(33);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(buffer, 0);
  buffer.writeUInt32BE(13, 8);
  buffer.write('IHDR', 12, 'ascii');
  buffer.writeUInt32BE(width, 16);
  buffer.writeUInt32BE(height, 20);
  buffer.writeUInt32BE(salt, 24);
  buffer.writeUInt32BE((salt ^ 0xa5a5a5a5) >>> 0, 28);
  buffer[32] = salt & 0xff;
  return buffer;
}

function makeFixture() {
  const frontendRoot = mkdtempSync(join(tmpdir(), 'mesh-checklist-evidence-'));
  let salt = 1;
  const rows = CHECKLIST_ROWS.map((row) => ({
    id: row.id,
    key: row.key,
    label: row.label,
    cells: Object.fromEntries(
      Object.entries(CHECKLIST_VARIANTS).map(([variantKey, variant]) => {
        const evidencePath = row.evidence[variantKey];
        const absolutePath = resolve(frontendRoot, evidencePath);
        const buffer = fakePng(variant.viewport.width, variant.viewport.height, salt++);
        mkdirSync(dirname(absolutePath), { recursive: true });
        writeFileSync(absolutePath, buffer);
        return [
          variantKey,
          {
            status: 'verified',
            evidence_path: evidencePath,
            sha256: sha256(buffer),
            byte_size: buffer.length,
            image: pngDimensions(buffer),
            provenance: {
              mode: variant.mode,
              theme: variant.theme,
              viewport: variant.viewport,
              route: row.route,
              backend_kind: row.provenance.backend_kind,
              database_provenance: false,
              generator: row.provenance.generator,
              source_readme: row.provenance.source_readme,
              ready_assertions: row.ready_assertions,
              shown_scope: row.shown_scope,
            },
          },
        ];
      }),
    ),
  }));
  return {
    frontendRoot,
    manifest: {
      schema_version: 1,
      issue: 'MES-128',
      source: 'docs/specs/frontend/competitor-parity-checklist.md#3',
      generated_by: 'test fixture',
      variants: structuredClone(CHECKLIST_VARIANTS),
      rows,
      summary: {
        total_cells: EXPECTED_CHECKLIST_CELLS,
        verified_cells: EXPECTED_CHECKLIST_CELLS,
        not_applicable_cells: 0,
        gap_cells: 0,
      },
    },
  };
}

function withFixture(run) {
  const fixture = makeFixture();
  try {
    run(fixture);
  } finally {
    rmSync(fixture.frontendRoot, { recursive: true, force: true });
  }
}

function assertRejected(fixture, pattern) {
  assert.throws(
    () => validateChecklistEvidence(fixture.manifest, { frontendRoot: fixture.frontendRoot }),
    (error) => error instanceof ChecklistEvidenceError && pattern.test(error.message),
  );
}

test('accepts exactly 28 rows × 4 viewport/theme cells', () => {
  withFixture((fixture) => {
    assert.deepEqual(
      validateChecklistEvidence(fixture.manifest, { frontendRoot: fixture.frontendRoot }),
      { total_cells: 112, verified_cells: 112, not_applicable_cells: 0, gap_cells: 0 },
    );
  });
});

test('fails closed when a cell is missing', () => {
  withFixture((fixture) => {
    delete fixture.manifest.rows[0].cells.mobile_dark;
    assertRejected(fixture, /row 1\/mobile_dark: cell is missing/);
  });
});

test('rejects a screenshot path reused for another checklist cell', () => {
  withFixture((fixture) => {
    fixture.manifest.rows[1].cells.desktop_light.evidence_path =
      fixture.manifest.rows[0].cells.desktop_light.evidence_path;
    assertRejected(fixture, /evidence path is reused/);
  });
});

test('rejects database provenance claims on mock screenshots', () => {
  withFixture((fixture) => {
    fixture.manifest.rows[0].cells.desktop_light.provenance.database_provenance = true;
    assertRejected(fixture, /must not claim database provenance/);
  });
});

test('rejects image dimensions that do not cover the declared viewport', () => {
  withFixture((fixture) => {
    const cell = fixture.manifest.rows[0].cells.desktop_light;
    const absolutePath = resolve(fixture.frontendRoot, cell.evidence_path);
    const buffer = fakePng(1439, 900, 9999);
    writeFileSync(absolutePath, buffer);
    cell.sha256 = sha256(buffer);
    cell.byte_size = buffer.length;
    cell.image = pngDimensions(buffer);
    assertRejected(fixture, /expected width 1440 and height >= 900/);
  });
});

test('rejects a stale SHA-256 digest', () => {
  withFixture((fixture) => {
    fixture.manifest.rows[0].cells.desktop_light.sha256 = '0'.repeat(64);
    assertRejected(fixture, /SHA-256 is stale or incorrect/);
  });
});

test('forbids N/A substitutions in the fixed 112-cell matrix', () => {
  withFixture((fixture) => {
    fixture.manifest.rows[0].cells.desktop_light.status = 'not_applicable';
    fixture.manifest.summary = {
      total_cells: 112,
      verified_cells: 111,
      not_applicable_cells: 1,
      gap_cells: 0,
    };
    assertRejected(fixture, /not_applicable is forbidden/);
  });
});
