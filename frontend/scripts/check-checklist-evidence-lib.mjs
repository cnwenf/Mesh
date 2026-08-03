import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { isAbsolute, normalize, resolve, sep } from 'node:path';
import {
  CHECKLIST_ROWS,
  CHECKLIST_VARIANTS,
  EXPECTED_CHECKLIST_CELLS,
} from './checklist-evidence-contract.mjs';

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const VARIANT_KEYS = Object.freeze(Object.keys(CHECKLIST_VARIANTS));

export class ChecklistEvidenceError extends Error {
  constructor(messages) {
    super(`checklist evidence validation failed:\n- ${messages.join('\n- ')}`);
    this.name = 'ChecklistEvidenceError';
    this.messages = messages;
  }
}

export function pngDimensions(buffer, label = 'PNG') {
  if (
    buffer.length < 24 ||
    !buffer.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
    buffer.toString('ascii', 12, 16) !== 'IHDR'
  ) {
    throw new Error(`${label} is not a readable PNG`);
  }
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (width < 1 || height < 1)
    throw new Error(`${label} has invalid ${width}x${height} dimensions`);
  return { width, height };
}

export function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex');
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, expected) {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === [...expected].sort()[index])
  );
}

function nonEmptyStrings(value) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' && item.trim() !== '')
  );
}

function safeEvidencePath(frontendRoot, relativePath) {
  if (typeof relativePath !== 'string' || relativePath.trim() === '' || isAbsolute(relativePath)) {
    return null;
  }
  const normalized = normalize(relativePath);
  if (normalized === '..' || normalized.startsWith(`..${sep}`)) return null;
  if (!normalized.startsWith(`e2e${sep}evidence${sep}`)) return null;
  const absolute = resolve(frontendRoot, normalized);
  const evidenceRoot = resolve(frontendRoot, 'e2e', 'evidence');
  if (!absolute.startsWith(`${evidenceRoot}${sep}`)) return null;
  return absolute;
}

/**
 * 校验 manifest 的结构、显式路径绑定、图片字节、视口/主题来源与唯一性。
 * 语义由 contract 中人工登记的 route + ready_assertions + shown_scope 绑定，
 * 门禁不从图片文件名推断。
 */
export function validateChecklistEvidence(manifest, { frontendRoot }) {
  const errors = [];
  const evidencePaths = new Map();
  const evidenceHashes = new Map();
  let verified = 0;
  let notApplicable = 0;

  if (!isRecord(manifest)) throw new ChecklistEvidenceError(['manifest must be an object']);
  if (manifest.schema_version !== 1) errors.push('schema_version must be 1');
  if (manifest.issue !== 'MES-128') errors.push('issue must be MES-128');
  if (manifest.source !== 'docs/specs/frontend/competitor-parity-checklist.md#3') {
    errors.push('source must point to competitor-parity-checklist.md#3');
  }
  if (!exactKeys(manifest.variants, VARIANT_KEYS)) {
    errors.push(`variants must contain exactly: ${VARIANT_KEYS.join(', ')}`);
  } else {
    for (const variantKey of VARIANT_KEYS) {
      const expected = CHECKLIST_VARIANTS[variantKey];
      const actual = manifest.variants[variantKey];
      if (
        !isRecord(actual) ||
        actual.mode !== expected.mode ||
        actual.theme !== expected.theme ||
        !isRecord(actual.viewport) ||
        actual.viewport.width !== expected.viewport.width ||
        actual.viewport.height !== expected.viewport.height
      ) {
        errors.push(
          `${variantKey}: variant provenance must be ${expected.mode}/${expected.theme} ${expected.viewport.width}x${expected.viewport.height}`,
        );
      }
    }
  }

  if (!Array.isArray(manifest.rows)) {
    errors.push('rows must be an array');
  } else {
    if (manifest.rows.length !== CHECKLIST_ROWS.length) {
      errors.push(`rows must contain exactly ${CHECKLIST_ROWS.length} entries`);
    }
    const seenRowIds = new Set();
    for (const expectedRow of CHECKLIST_ROWS) {
      const actualRow = manifest.rows.find(
        (candidate) => isRecord(candidate) && candidate.id === expectedRow.id,
      );
      if (actualRow === undefined) {
        errors.push(`row ${expectedRow.id} (${expectedRow.key}) is missing`);
        continue;
      }
      if (seenRowIds.has(actualRow.id)) errors.push(`row ${actualRow.id} is duplicated`);
      seenRowIds.add(actualRow.id);
      if (actualRow.key !== expectedRow.key || actualRow.label !== expectedRow.label) {
        errors.push(`row ${expectedRow.id}: key/label differs from the fixed checklist contract`);
      }
      if (!exactKeys(actualRow.cells, VARIANT_KEYS)) {
        errors.push(`row ${expectedRow.id}: cells must contain exactly ${VARIANT_KEYS.join(', ')}`);
      }

      for (const variantKey of VARIANT_KEYS) {
        const cellLabel = `row ${expectedRow.id}/${variantKey}`;
        const cell = isRecord(actualRow.cells) ? actualRow.cells[variantKey] : undefined;
        if (!isRecord(cell)) {
          errors.push(`${cellLabel}: cell is missing`);
          continue;
        }
        if (cell.status === 'not_applicable') {
          notApplicable += 1;
          errors.push(`${cellLabel}: not_applicable is forbidden for this 28×4 checklist`);
          continue;
        }
        if (cell.status !== 'verified') {
          errors.push(`${cellLabel}: status must be verified (received ${String(cell.status)})`);
          continue;
        }
        verified += 1;

        const expectedPath = expectedRow.evidence[variantKey];
        if (cell.evidence_path !== expectedPath) {
          errors.push(
            `${cellLabel}: evidence_path must match its explicit contract path ${expectedPath}`,
          );
        }
        const absolutePath = safeEvidencePath(frontendRoot, cell.evidence_path);
        if (absolutePath === null) {
          errors.push(
            `${cellLabel}: evidence_path must be a safe repo-relative path under e2e/evidence`,
          );
          continue;
        }
        if (evidencePaths.has(cell.evidence_path)) {
          errors.push(
            `${cellLabel}: evidence path is reused by ${evidencePaths.get(cell.evidence_path)}`,
          );
        } else {
          evidencePaths.set(cell.evidence_path, cellLabel);
        }

        const expectedVariant = CHECKLIST_VARIANTS[variantKey];
        const provenance = cell.provenance;
        if (!isRecord(provenance)) {
          errors.push(`${cellLabel}: provenance is missing`);
        } else {
          if (
            provenance.mode !== expectedVariant.mode ||
            provenance.theme !== expectedVariant.theme ||
            !isRecord(provenance.viewport) ||
            provenance.viewport.width !== expectedVariant.viewport.width ||
            provenance.viewport.height !== expectedVariant.viewport.height
          ) {
            errors.push(`${cellLabel}: viewport/theme provenance does not match its variant`);
          }
          if (provenance.route !== expectedRow.route)
            errors.push(`${cellLabel}: route provenance differs from the explicit row contract`);
          if (provenance.generator !== expectedRow.provenance.generator)
            errors.push(
              `${cellLabel}: generator provenance differs from the explicit row contract`,
            );
          if (provenance.source_readme !== expectedRow.provenance.source_readme)
            errors.push(
              `${cellLabel}: source_readme provenance differs from the explicit row contract`,
            );
          if (provenance.backend_kind !== expectedRow.provenance.backend_kind)
            errors.push(
              `${cellLabel}: backend_kind provenance differs from the explicit row contract`,
            );
          if (provenance.database_provenance !== false) {
            errors.push(`${cellLabel}: screenshot evidence must not claim database provenance`);
          }
          if (
            JSON.stringify(provenance.ready_assertions) !==
              JSON.stringify(expectedRow.ready_assertions) ||
            !nonEmptyStrings(provenance.ready_assertions)
          ) {
            errors.push(`${cellLabel}: ready_assertions differ from the explicit row contract`);
          }
          if (
            JSON.stringify(provenance.shown_scope) !== JSON.stringify(expectedRow.shown_scope) ||
            !nonEmptyStrings(provenance.shown_scope)
          ) {
            errors.push(`${cellLabel}: shown_scope differs from the explicit row contract`);
          }
        }

        if (!existsSync(absolutePath) || !statSync(absolutePath).isFile()) {
          errors.push(`${cellLabel}: evidence file does not exist: ${cell.evidence_path}`);
          continue;
        }
        const buffer = readFileSync(absolutePath);
        let dimensions;
        try {
          dimensions = pngDimensions(buffer, cell.evidence_path);
        } catch (error) {
          errors.push(`${cellLabel}: ${error instanceof Error ? error.message : String(error)}`);
          continue;
        }
        const actualHash = sha256(buffer);
        if (cell.sha256 !== actualHash) errors.push(`${cellLabel}: SHA-256 is stale or incorrect`);
        if (cell.byte_size !== buffer.length)
          errors.push(`${cellLabel}: byte_size is stale or incorrect`);
        if (
          !isRecord(cell.image) ||
          cell.image.width !== dimensions.width ||
          cell.image.height !== dimensions.height
        ) {
          errors.push(`${cellLabel}: recorded image dimensions are stale or incorrect`);
        }
        if (
          dimensions.width !== expectedVariant.viewport.width ||
          dimensions.height < expectedVariant.viewport.height
        ) {
          errors.push(
            `${cellLabel}: image is ${dimensions.width}x${dimensions.height}; expected width ${expectedVariant.viewport.width} and height >= ${expectedVariant.viewport.height}`,
          );
        }
        if (evidenceHashes.has(actualHash)) {
          errors.push(`${cellLabel}: screenshot bytes duplicate ${evidenceHashes.get(actualHash)}`);
        } else {
          evidenceHashes.set(actualHash, cellLabel);
        }
      }
    }

    const extraRows = manifest.rows.filter(
      (candidate) => !isRecord(candidate) || !CHECKLIST_ROWS.some((row) => row.id === candidate.id),
    );
    if (extraRows.length > 0)
      errors.push(`manifest contains ${extraRows.length} unexpected row(s)`);
  }

  const computedSummary = {
    total_cells: EXPECTED_CHECKLIST_CELLS,
    verified_cells: verified,
    not_applicable_cells: notApplicable,
    gap_cells: EXPECTED_CHECKLIST_CELLS - verified - notApplicable,
  };
  if (JSON.stringify(manifest.summary) !== JSON.stringify(computedSummary)) {
    errors.push(`summary must equal computed values ${JSON.stringify(computedSummary)}`);
  }
  if (computedSummary.verified_cells !== EXPECTED_CHECKLIST_CELLS) {
    errors.push(`fail-closed matrix has ${computedSummary.gap_cells} gap cell(s)`);
  }
  if (errors.length > 0) throw new ChecklistEvidenceError(errors);
  return computedSummary;
}
