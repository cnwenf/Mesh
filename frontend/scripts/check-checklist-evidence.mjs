import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateChecklistEvidence } from './check-checklist-evidence-lib.mjs';

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const manifestPath = resolve(frontendRoot, 'e2e/evidence/mes128-checklist/manifest.json');

try {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const summary = validateChecklistEvidence(manifest, { frontendRoot });
  console.log(
    `[checklist-evidence] OK: ${summary.verified_cells}/${summary.total_cells} verified, ` +
      `${summary.not_applicable_cells} N/A, ${summary.gap_cells} gaps`,
  );
} catch (error) {
  console.error(
    `[checklist-evidence] FAIL: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exitCode = 1;
}
