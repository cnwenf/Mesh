import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { format } from 'prettier';
import {
  CHECKLIST_ROWS,
  CHECKLIST_VARIANTS,
  EXPECTED_CHECKLIST_CELLS,
} from './checklist-evidence-contract.mjs';
import { pngDimensions, sha256 } from './check-checklist-evidence-lib.mjs';

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const manifestPath = resolve(frontendRoot, 'e2e/evidence/mes128-checklist/manifest.json');

const rows = CHECKLIST_ROWS.map((row) => ({
  id: row.id,
  key: row.key,
  label: row.label,
  cells: Object.fromEntries(
    Object.entries(CHECKLIST_VARIANTS).map(([variantKey, variant]) => {
      const evidencePath = row.evidence[variantKey];
      const absolutePath = resolve(frontendRoot, evidencePath);
      const buffer = readFileSync(absolutePath);
      return [
        variantKey,
        {
          status: 'verified',
          evidence_path: evidencePath,
          sha256: sha256(buffer),
          byte_size: buffer.length,
          image: pngDimensions(buffer, evidencePath),
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

const manifest = {
  schema_version: 1,
  issue: 'MES-128',
  source: 'docs/specs/frontend/competitor-parity-checklist.md#3',
  generated_by: 'scripts/generate-checklist-evidence-manifest.mjs',
  variants: CHECKLIST_VARIANTS,
  rows,
  summary: {
    total_cells: EXPECTED_CHECKLIST_CELLS,
    verified_cells: EXPECTED_CHECKLIST_CELLS,
    not_applicable_cells: 0,
    gap_cells: 0,
  },
};

mkdirSync(dirname(manifestPath), { recursive: true });
writeFileSync(
  manifestPath,
  await format(JSON.stringify(manifest), { parser: 'json', printWidth: 100, tabWidth: 2 }),
  'utf8',
);
console.log(`[checklist-evidence] wrote ${manifestPath} (${EXPECTED_CHECKLIST_CELLS} cells)`);
