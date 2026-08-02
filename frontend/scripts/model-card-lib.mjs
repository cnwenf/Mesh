import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { isAbsolute, relative, resolve } from 'node:path';
import { inflateSync } from 'node:zlib';

export const BLUEPRINT_PAGE_IDS = Object.freeze([
  'auth-login',
  'auth-register',
  'auth-code',
  'inbox',
  'chat',
  'my-issues',
  'issues',
  'board',
  'issue-detail',
  'projects',
  'project-detail',
  'members',
  'agents',
  'agent-detail',
  'skills',
  'skill-detail',
  'autopilots',
  'squads',
  'runtimes',
  'analytics',
  'settings',
  'state-gallery',
]);

export const BLUEPRINT_TOKEN_IDS = Object.freeze([
  '--shell',
  '--canvas',
  '--surface',
  '--raised',
  '--hover',
  '--selected',
  '--ink',
  '--ink-soft',
  '--ink-faint',
  '--line',
  '--line-strong',
  '--input-line',
  '--primary',
  '--primary-ink',
  '--disabled',
  '--disabled-ink',
  '--brand',
  '--brand-soft',
  '--success',
  '--success-soft',
  '--warning',
  '--warning-soft',
  '--danger',
  '--danger-soft',
  '--info',
  '--info-soft',
  '--violet',
  '--orange',
  '--shadow-surface',
  '--shadow-menu',
  '--shadow-float',
  '--radius-xs',
  '--radius-sm',
  '--radius-md',
  '--radius-lg',
  '--radius-xl',
  '--radius-round',
  '--sidebar-size',
  '--page-header',
  '--ease',
  '--font-ui',
  '--font-mono',
]);

export const REQUIRED_PAGE_STATES = Object.freeze(['default', 'loading', 'empty', 'error']);

export const REQUIRED_COMPONENT_IDS = Object.freeze([
  'app-shell',
  'public-flow-shell',
  'page-header',
  'breadcrumb',
  'data-view',
  'detail-layout',
  'conversation-layout',
  'settings-layout',
  'surface-card',
  'progress',
  'kpi-card',
  'button',
  'input',
  'select',
  'badge',
  'avatar',
  'icon',
  'tabs',
  'menu',
  'dialog',
  'drawer',
  'popover',
  'tooltip',
  'empty-state',
  'error-state',
  'skeleton',
  'status-dot',
  'data-table',
  'toolbar',
  'command-palette',
  'toast',
]);

const STRATEGIES = new Set(['reuse', 'calibrate', 'add', 'remove']);
const RECONCILIATION = new Set(['reused', 'calibrated', 'new', 'removed', 'pending', 'blocked']);
const STATE_STATUSES = new Set(['verified', 'pending', 'blocked', 'not-applicable']);
const BASELINE_DISPOSITIONS = new Set(['active', 'cancelled', 'superseded']);
const BASELINE_ADOPTIONS = new Set(['authoritative', 'partial-input', 'discarded']);
const REQUIRED_THEMES = ['light', 'dark'];
const REQUIRED_VIEWPORTS = ['390x844', '1440x900'];
const REQUIRED_INPUT_MODES = ['mouse', 'keyboard', 'touch'];
const DEFAULT_ROUTE_SOURCE = 'src/App.tsx';
const DEFAULT_LEGACY_ROUTE_SOURCE = 'src/workspace/flatRoutes.tsx';
const DEFAULT_TOKEN_SOURCES = [
  'src/design/tokens.css',
  'src/design/tokens-dark.css',
  'src/design/typography.css',
  'src/design/base.css',
];
const NESTED_ROUTE_PARENTS = new Map([
  ['appearance', 'settings'],
  ['notifications', 'settings'],
  ['security', 'settings'],
  ['general', 'w/:workspaceSlug/settings'],
  ['invitations', 'w/:workspaceSlug/settings'],
  ['roles', 'w/:workspaceSlug/settings'],
  ['labels', 'w/:workspaceSlug/settings'],
  ['custom-fields', 'w/:workspaceSlug/settings'],
  ['data', 'w/:workspaceSlug/settings'],
  ['tokens', 'w/:workspaceSlug/settings'],
  ['audit', 'w/:workspaceSlug/settings'],
  ['danger', 'w/:workspaceSlug/settings'],
]);
const REQUIRED_ALIAS_ROUTES = new Map([
  ['my-issues', { target: 'issues', routes: ['/w/:workspaceSlug/issues?mine=true'] }],
  ['agents', { target: 'members', routes: ['/w/:workspaceSlug/members?member_type=agent'] }],
]);

function duplicates(values) {
  const seen = new Set();
  const duplicateValues = new Set();
  for (const value of values) {
    if (seen.has(value)) duplicateValues.add(value);
    seen.add(value);
  }
  return [...duplicateValues].sort();
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function extractCssCustomPropertyNames(source) {
  return new Set([...source.matchAll(/^\s*(--[a-zA-Z0-9_-]+)\s*:/gmu)].map((match) => match[1]));
}

function readSources(frontendRoot, paths, errors, label) {
  let source = '';
  for (const path of paths) {
    const target = resolve(frontendRoot, path);
    if (!existsSync(target)) {
      errors.push(`${label}: missing source file ${path}`);
      continue;
    }
    source += `\n${readFileSync(target, 'utf8')}`;
  }
  return source;
}

function validatePath(frontendRoot, path, owner, errors) {
  if (typeof path !== 'string' || path.length === 0) {
    errors.push(`${owner}: file path must be a non-empty string`);
    return;
  }
  if (isAbsolute(path) || path.split(/[\\/]/u).includes('..')) {
    errors.push(`${owner}: file path must stay inside frontend root: ${path}`);
    return;
  }
  const target = resolve(frontendRoot, path);
  const rel = relative(frontendRoot, target);
  if (rel.startsWith('..') || isAbsolute(rel)) {
    errors.push(`${owner}: file path escapes frontend root: ${path}`);
  } else if (!existsSync(target)) {
    errors.push(`${owner}: missing file ${path}`);
  }
}

function validateEnum(value, allowed, owner, kind, errors) {
  if (!allowed.has(value)) errors.push(`${owner}: invalid ${kind} ${String(value)}`);
}

function visualCell(viewport, theme, state) {
  return `${viewport}|${theme}|${state}`;
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function visualEnvironmentSha256(card) {
  return sha256(JSON.stringify(card.visualEnvironment));
}

const PNG_CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

function pngCrc32(content) {
  let value = 0xffffffff;
  for (const byte of content) value = PNG_CRC_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8);
  return (value ^ 0xffffffff) >>> 0;
}

function inspectPng(buffer) {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (buffer.length < 57 || !buffer.subarray(0, signature.length).equals(signature)) return null;

  let offset = signature.length;
  let width;
  let height;
  let colorType;
  const idat = [];
  let sawIend = false;
  while (offset < buffer.length) {
    if (offset + 12 > buffer.length) return null;
    const length = buffer.readUInt32BE(offset);
    const typeStart = offset + 4;
    const dataStart = typeStart + 4;
    const dataEnd = dataStart + length;
    const chunkEnd = dataEnd + 4;
    if (chunkEnd > buffer.length) return null;
    const type = buffer.toString('ascii', typeStart, dataStart);
    if (pngCrc32(buffer.subarray(typeStart, dataEnd)) !== buffer.readUInt32BE(dataEnd)) {
      return null;
    }
    if (type === 'IHDR') {
      if (offset !== signature.length || length !== 13) return null;
      width = buffer.readUInt32BE(dataStart);
      height = buffer.readUInt32BE(dataStart + 4);
      const bitDepth = buffer[dataStart + 8];
      colorType = buffer[dataStart + 9];
      const compression = buffer[dataStart + 10];
      const filter = buffer[dataStart + 11];
      const interlace = buffer[dataStart + 12];
      if (
        width === 0 ||
        height === 0 ||
        bitDepth !== 8 ||
        colorType !== 6 ||
        compression !== 0 ||
        filter !== 0 ||
        interlace !== 0
      ) {
        return null;
      }
    } else if (type === 'IDAT') {
      idat.push(buffer.subarray(dataStart, dataEnd));
    } else if (type === 'IEND') {
      if (length !== 0 || chunkEnd !== buffer.length) return null;
      sawIend = true;
    }
    offset = chunkEnd;
  }
  if (!sawIend || width === undefined || height === undefined || idat.length === 0) return null;

  const channels = 4;
  const rowLength = width * channels + 1;
  const expectedLength = rowLength * height;
  if (!Number.isSafeInteger(expectedLength) || expectedLength > 100_000_000) return null;
  let pixels;
  try {
    pixels = inflateSync(Buffer.concat(idat), { maxOutputLength: expectedLength });
  } catch {
    return null;
  }
  if (pixels.length !== expectedLength) return null;
  const rgba = Buffer.alloc(width * height * channels);
  const bytesPerRow = width * channels;
  const paeth = (left, up, upperLeft) => {
    const estimate = left + up - upperLeft;
    const leftDistance = Math.abs(estimate - left);
    const upDistance = Math.abs(estimate - up);
    const upperLeftDistance = Math.abs(estimate - upperLeft);
    if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return left;
    return upDistance <= upperLeftDistance ? up : upperLeft;
  };
  for (let row = 0; row < height; row += 1) {
    const filterType = pixels[row * rowLength];
    if (filterType > 4) return null;
    const targetOffset = row * bytesPerRow;
    const sourceOffset = row * rowLength + 1;
    for (let column = 0; column < bytesPerRow; column += 1) {
      const left = column >= channels ? rgba[targetOffset + column - channels] : 0;
      const up = row > 0 ? rgba[targetOffset + column - bytesPerRow] : 0;
      const upperLeft =
        row > 0 && column >= channels ? rgba[targetOffset + column - bytesPerRow - channels] : 0;
      const predictor = [0, left, up, Math.floor((left + up) / 2), paeth(left, up, upperLeft)][
        filterType
      ];
      rgba[targetOffset + column] = (pixels[sourceOffset + column] + predictor) & 0xff;
    }
  }
  return { width, height, rgba };
}

export function comparePngBuffers(actualBuffer, baselineBuffer) {
  const actual = inspectPng(actualBuffer);
  const baseline = inspectPng(baselineBuffer);
  if (
    actual === null ||
    baseline === null ||
    actual.width !== baseline.width ||
    actual.height !== baseline.height
  ) {
    return null;
  }
  let diffPixels = 0;
  for (let pixel = 0; pixel < actual.width * actual.height; pixel += 1) {
    const offset = pixel * 4;
    if (
      !actual.rgba.subarray(offset, offset + 4).equals(baseline.rgba.subarray(offset, offset + 4))
    ) {
      diffPixels += 1;
    }
  }
  return {
    width: actual.width,
    height: actual.height,
    totalPixels: actual.width * actual.height,
    diffPixels,
  };
}

function readQuotedLiteral(source, start) {
  const quote = source[start];
  if (quote !== "'" && quote !== '"') return null;
  let value = '';
  for (let index = start + 1; index < source.length; index += 1) {
    const character = source[index];
    if (character === quote) return { value, end: index + 1 };
    if (character === '\\') {
      index += 1;
      if (index >= source.length) return null;
      const escaped = source[index];
      value +=
        {
          n: '\n',
          r: '\r',
          t: '\t',
        }[escaped] ?? escaped;
    } else {
      value += character;
    }
  }
  return null;
}

function findTestCaseSource(source, testTitle) {
  const invocations = [...source.matchAll(/(?:^|\n)\s*test\s*\(/gmu)];
  for (const [index, invocation] of invocations.entries()) {
    let cursor = (invocation.index ?? 0) + invocation[0].length;
    while (/\s/u.test(source[cursor] ?? '')) cursor += 1;
    const title = readQuotedLiteral(source, cursor);
    if (title === null || title.value !== testTitle) continue;
    cursor = title.end;
    while (/\s/u.test(source[cursor] ?? '')) cursor += 1;
    if (source[cursor] !== ',') continue;
    const start = invocation.index ?? 0;
    const end = invocations[index + 1]?.index ?? source.length;
    return source.slice(start, end);
  }
  return null;
}

function executableSource(source) {
  let result = '';
  let state = 'code';
  let escaped = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
    if (state === 'code') {
      if (character === '/' && next === '/') {
        result += '  ';
        index += 1;
        state = 'line-comment';
      } else if (character === '/' && next === '*') {
        result += '  ';
        index += 1;
        state = 'block-comment';
      } else if (character === "'" || character === '"' || character === '`') {
        result += ' ';
        state = character;
      } else {
        result += character;
      }
    } else if (state === 'line-comment') {
      result += character === '\n' ? '\n' : ' ';
      if (character === '\n') state = 'code';
    } else if (state === 'block-comment') {
      if (character === '*' && next === '/') {
        result += '  ';
        index += 1;
        state = 'code';
      } else {
        result += character === '\n' ? '\n' : ' ';
      }
    } else {
      result += character === '\n' ? '\n' : ' ';
      if (escaped) {
        escaped = false;
      } else if (character === '\\') {
        escaped = true;
      } else if (character === state) {
        state = 'code';
      }
    }
  }
  return result;
}

function testExercisesInputMode(testSource, mode) {
  const patterns = {
    mouse:
      /(?:\)|\b[A-Za-z_$][\w$]*)\.(?:click|dblclick|dragTo|hover)\s*\(|\bmouse\.(?:click|down|move|up)\s*\(/u,
    keyboard:
      /(?:\)|\b[A-Za-z_$][\w$]*)\.(?:press|pressSequentially|type)\s*\(|\bkeyboard\.(?:down|insertText|press|type|up)\s*\(/u,
    touch: /(?:\)|\b[A-Za-z_$][\w$]*)\.tap\s*\(|\btouchscreen\.tap\s*\(/u,
  };
  return patterns[mode]?.test(executableSource(testSource)) === true;
}

function testCapturesVisualPath(testSource, path) {
  const marker = 'MES108_CLAIMED_SCREENSHOT_PATH';
  let marked = testSource;
  for (const quote of ["'", '"', '`']) {
    marked = marked.replaceAll(`${quote}${path}${quote}`, marker);
  }
  return new RegExp(`\\bmes108Screenshot\\.capture\\s*\\(\\s*${marker}(?:\\s*[,\\)])`, 'u').test(
    executableSource(marked),
  );
}

function validatePlaywrightTarget(target, frontendRoot, owner, errors) {
  if (target.config !== 'playwright.mes108.config.ts') {
    errors.push(`${owner}: Playwright config must equal playwright.mes108.config.ts`);
  } else {
    validatePath(frontendRoot, target.config, owner, errors);
  }
  if (!['phone', 'wide'].includes(target.project)) {
    errors.push(`${owner}: Playwright project must be phone or wide`);
  }
}

function validateVisualCapture(capture, artifactPath, viewport, card, frontendRoot, owner, errors) {
  if (!isRecord(capture)) {
    errors.push(`${owner}: visual artifact capture must be an object`);
    return;
  }
  if (capture.runner !== 'playwright') {
    errors.push(`${owner}: visual artifact capture.runner must equal playwright`);
  }
  validatePlaywrightTarget(capture, frontendRoot, owner, errors);
  const expectedProject = { '390x844': 'phone', '1440x900': 'wide' }[viewport];
  if (expectedProject !== undefined && capture.project !== expectedProject) {
    errors.push(`${owner}: visual artifact capture.project does not match viewport ${viewport}`);
  }
  if (
    typeof capture.spec !== 'string' ||
    !capture.spec.startsWith('e2e/') ||
    !capture.spec.endsWith('.spec.ts')
  ) {
    errors.push(`${owner}: visual artifact capture.spec must be an e2e spec`);
  } else {
    validatePath(frontendRoot, capture.spec, owner, errors);
    const target = resolve(frontendRoot, capture.spec);
    if (existsSync(target)) {
      const source = readFileSync(target, 'utf8');
      const fixtureImport =
        /import\s*\{(?<names>[^}]*)\}\s*from\s*['"][^'"]*mes108-evidence-fixture\.mjs['"]/u.exec(
          source,
        );
      const fixtureNames =
        fixtureImport?.groups?.names
          ?.split(',')
          .map((name) => name.trim())
          .filter(Boolean) ?? [];
      if (
        !fixtureNames.includes('test') ||
        !fixtureNames.includes('expect') ||
        /from\s*['"]@playwright\/test['"]/u.test(source)
      ) {
        errors.push(
          `${owner}: visual artifact capture spec must import test and expect from mes108-evidence-fixture.mjs`,
        );
      }
      const testSource =
        typeof capture.testTitle === 'string'
          ? findTestCaseSource(source, capture.testTitle)
          : null;
      if (testSource === null) {
        errors.push(`${owner}: visual artifact capture.testTitle was not found`);
      } else {
        const executable = executableSource(testSource);
        if (!/(?:\)|\b[A-Za-z_$][\w$]*)\.toHaveScreenshot\s*\(/u.test(executable)) {
          errors.push(`${owner}: visual artifact capture test does not compare a screenshot`);
        }
        if (typeof artifactPath !== 'string' || !testCapturesVisualPath(testSource, artifactPath)) {
          errors.push(
            `${owner}: visual artifact capture test does not capture claimed screenshot path`,
          );
        }
      }
    }
  }
  if (
    typeof capture.capturedAt !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(capture.capturedAt) ||
    Number.isNaN(Date.parse(capture.capturedAt))
  ) {
    errors.push(`${owner}: visual artifact capture.capturedAt must be a UTC timestamp`);
  }
  if (capture.environmentSha256 !== visualEnvironmentSha256(card)) {
    errors.push(`${owner}: visual artifact capture environment digest does not match`);
  }
}

function validateVisualComparison(
  comparison,
  actualContent,
  actualSha,
  viewport,
  card,
  frontendRoot,
  registry,
  cell,
  owner,
  errors,
) {
  if (!isRecord(comparison)) {
    errors.push(`${owner}: visual artifact comparison must be an object`);
    return;
  }
  let computed = null;
  const baselinePath = comparison.baselinePath;
  if (
    typeof baselinePath !== 'string' ||
    !baselinePath.startsWith('e2e/evidence/mes108/baselines/') ||
    !/\.png$/iu.test(baselinePath)
  ) {
    errors.push(`${owner}: visual artifact comparison.baselinePath is invalid`);
  } else {
    validatePath(frontendRoot, baselinePath, owner, errors);
    const baselineTarget = resolve(frontendRoot, baselinePath);
    if (existsSync(baselineTarget)) {
      const baselineContent = readFileSync(baselineTarget);
      const baselineImage = inspectPng(baselineContent);
      if (baselineImage === null) {
        errors.push(`${owner}: invalid baseline PNG content ${baselinePath}`);
      } else {
        const expected = /^(\d+)x(\d+)$/u.exec(viewport ?? '');
        if (
          expected !== null &&
          (baselineImage.width !== Number(expected[1]) ||
            baselineImage.height !== Number(expected[2]))
        ) {
          errors.push(`${owner}: baseline dimensions do not match ${String(viewport)}`);
        }
      }
      const baselineSha = sha256(baselineContent);
      if (comparison.baselineSha256 !== baselineSha) {
        errors.push(`${owner}: visual artifact comparison.baselineSha256 does not match`);
      }
      registerVisualArtifact(registry, baselinePath, baselineSha, cell, owner, errors, 'baseline');
      computed = comparePngBuffers(actualContent, baselineContent);
    }
  }
  if (comparison.actualSha256 !== actualSha) {
    errors.push(`${owner}: visual artifact comparison.actualSha256 does not match`);
  }
  if (comparison.algorithm !== 'rgba-exact-v1') {
    errors.push(`${owner}: visual artifact comparison.algorithm must equal rgba-exact-v1`);
  }
  if (computed !== null && comparison.totalPixels !== computed.totalPixels) {
    errors.push(`${owner}: visual artifact comparison.totalPixels does not match pixels`);
  }
  if (computed !== null && comparison.diffPixels !== computed.diffPixels) {
    errors.push(`${owner}: visual artifact comparison.diffPixels does not match pixels`);
  }
  if (
    typeof comparison.threshold !== 'number' ||
    !Number.isFinite(comparison.threshold) ||
    comparison.threshold < 0 ||
    comparison.threshold > 1
  ) {
    errors.push(`${owner}: visual artifact comparison.threshold must be between 0 and 1`);
  } else if (
    computed !== null &&
    computed.totalPixels > 0 &&
    computed.diffPixels / computed.totalPixels > comparison.threshold
  ) {
    errors.push(`${owner}: visual artifact comparison exceeds its threshold`);
  }
  if (!['matched', 'approved-difference'].includes(comparison.status)) {
    errors.push(`${owner}: visual artifact comparison.status is invalid`);
  }
  if (comparison.status === 'matched' && computed?.diffPixels !== 0) {
    errors.push(`${owner}: matched visual comparison must have zero pixel differences`);
  }
  if (comparison.status === 'approved-difference') {
    const risk = card.calibrationRisks?.find((candidate) => candidate?.id === comparison.riskId);
    if (risk === undefined || ['pending', 'blocked'].includes(risk.status)) {
      errors.push(`${owner}: approved visual difference requires a resolved calibration risk`);
    }
    if (typeof comparison.reason !== 'string' || comparison.reason.trim().length === 0) {
      errors.push(`${owner}: approved visual difference requires a reason`);
    }
    if (computed?.diffPixels === 0) {
      errors.push(`${owner}: approved visual difference must contain a pixel difference`);
    }
  }
}

function registerVisualArtifact(registry, path, digest, cell, owner, errors, namespace = 'actual') {
  for (const [kind, value] of [
    ['path', path],
    ['sha256', digest],
  ]) {
    if (typeof value !== 'string') continue;
    const key =
      namespace === 'actual' ? kind : `${namespace}${kind[0].toUpperCase()}${kind.slice(1)}`;
    const prior = registry[key].get(value);
    if (prior !== undefined && prior !== `${owner}:${cell}`) {
      const label = namespace === 'actual' ? 'visual artifact' : `visual ${namespace} artifact`;
      errors.push(`${owner}: global duplicate ${label} ${kind} ${value} (also ${prior})`);
    } else {
      registry[key].set(value, `${owner}:${cell}`);
    }
  }
}

function validateVisualArtifact(
  artifact,
  evidenceCells,
  card,
  frontendRoot,
  registry,
  owner,
  errors,
) {
  if (!isRecord(artifact)) {
    errors.push(`${owner}: visual artifact must be an object`);
    return null;
  }
  const { viewport, theme, state, path } = artifact;
  const cell = visualCell(viewport, theme, state);
  if (!evidenceCells.has(cell)) {
    errors.push(`${owner}: visual artifact references unknown evidence cell ${cell}`);
  }
  if (
    typeof path !== 'string' ||
    !path.startsWith('e2e/evidence/mes108/') ||
    path.startsWith('e2e/evidence/mes108/baselines/') ||
    !/\.png$/iu.test(path)
  ) {
    errors.push(
      `${owner}: visual artifact must be an image under e2e/evidence/mes108: ${String(path)}`,
    );
  } else {
    validatePath(frontendRoot, path, owner, errors);
    const target = resolve(frontendRoot, path);
    if (existsSync(target)) {
      const content = readFileSync(target);
      const image = inspectPng(content);
      if (image === null) {
        errors.push(`${owner}: invalid PNG content ${path}`);
      } else {
        const expected = /^(\d+)x(\d+)$/u.exec(viewport ?? '');
        if (
          expected !== null &&
          (image.width !== Number(expected[1]) || image.height !== Number(expected[2]))
        ) {
          errors.push(`${owner}: visual artifact dimensions do not match ${String(viewport)}`);
        }
      }
      const digest = sha256(content);
      if (!/^[0-9a-f]{64}$/u.test(artifact.sha256 ?? '') || artifact.sha256 !== digest) {
        errors.push(`${owner}: visual artifact sha256 does not match ${path}`);
      }
      registerVisualArtifact(registry, path, digest, cell, owner, errors);
      validateVisualComparison(
        artifact.comparison,
        content,
        digest,
        viewport,
        card,
        frontendRoot,
        registry,
        cell,
        owner,
        errors,
      );
    }
  }
  validateVisualCapture(artifact.capture, path, viewport, card, frontendRoot, owner, errors);
  return cell;
}

function validateVisualEvidence(surface, card, frontendRoot, registry, owner, errors) {
  if (!Array.isArray(surface.visualEvidence) || surface.visualEvidence.length === 0) {
    errors.push(`${owner}: visualEvidence must be a non-empty array`);
    return;
  }

  const expected = new Set();
  for (const viewport of card.dimensions.viewports ?? []) {
    for (const theme of card.dimensions.themes ?? []) {
      for (const state of card.dimensions.states ?? []) {
        expected.add(visualCell(viewport, theme, state));
      }
    }
  }

  const covered = new Map();
  for (const evidence of surface.visualEvidence) {
    if (!isRecord(evidence)) {
      errors.push(`${owner}: invalid visual evidence entry`);
      continue;
    }
    validateEnum(evidence.status, STATE_STATUSES, owner, 'visual evidence status', errors);
    if (
      ['blocked', 'not-applicable'].includes(evidence.status) &&
      (typeof evidence.reason !== 'string' || evidence.reason.trim().length === 0)
    ) {
      errors.push(`${owner}: ${evidence.status} visual evidence requires a reason`);
    }
    for (const key of ['viewports', 'themes', 'states']) {
      if (!Array.isArray(evidence[key]) || evidence[key].length === 0) {
        errors.push(`${owner}: visual evidence ${key} must be a non-empty array`);
      }
    }
    if (
      !Array.isArray(evidence.viewports) ||
      !Array.isArray(evidence.themes) ||
      !Array.isArray(evidence.states)
    ) {
      continue;
    }
    const evidenceCells = new Set();
    for (const viewport of evidence.viewports) {
      for (const theme of evidence.themes) {
        for (const state of evidence.states) {
          const cell = visualCell(viewport, theme, state);
          evidenceCells.add(cell);
          if (!expected.has(cell)) {
            errors.push(`${owner}: unknown visual evidence cell ${cell}`);
          } else if (covered.has(cell)) {
            errors.push(`${owner}: duplicate visual evidence cell ${cell}`);
          } else {
            covered.set(cell, evidence.status);
          }
          if (
            evidence.status === 'not-applicable' &&
            (state === 'default' || surface.states?.[state] !== 'not-applicable')
          ) {
            errors.push(`${owner}: not-applicable visual cell ${cell} has an applicable state`);
          }
          if (
            evidence.status !== 'not-applicable' &&
            surface.states?.[state] === 'not-applicable'
          ) {
            errors.push(`${owner}: state ${state} requires not-applicable visual cell ${cell}`);
          }
        }
      }
    }
    if (evidence.status === 'verified') {
      if (!Array.isArray(evidence.artifacts) || evidence.artifacts.length === 0) {
        errors.push(`${owner}: verified visual evidence requires artifacts`);
      } else {
        const artifactCells = [];
        const artifactPaths = [];
        for (const artifact of evidence.artifacts) {
          const cell = validateVisualArtifact(
            artifact,
            evidenceCells,
            card,
            frontendRoot,
            registry,
            owner,
            errors,
          );
          if (cell !== null) artifactCells.push(cell);
          if (isRecord(artifact) && typeof artifact.path === 'string') {
            artifactPaths.push(artifact.path);
          }
        }
        for (const cell of duplicates(artifactCells)) {
          errors.push(`${owner}: duplicate visual artifact cell ${cell}`);
        }
        for (const path of duplicates(artifactPaths)) {
          errors.push(`${owner}: duplicate visual artifact path ${path}`);
        }
        if (
          artifactCells.length !== evidenceCells.size ||
          [...evidenceCells].some((cell) => !artifactCells.includes(cell))
        ) {
          errors.push(`${owner}: verified evidence requires one unique artifact per visual cell`);
        }
      }
    }
  }

  for (const cell of expected) {
    if (!covered.has(cell)) errors.push(`${owner}: missing visual evidence cell ${cell}`);
  }
}

function normalizedRoutePath(route) {
  const path = route.split(/[?#]/u, 1)[0];
  if (path === '/' || path === '*') return path;
  return path.replace(/^\/+|\/+$/gu, '');
}

function canonicalRouteForFragment(fragment) {
  const source = normalizedRoutePath(fragment);
  if (source === '/' || source === '*') return source;
  const parent = NESTED_ROUTE_PARENTS.get(source);
  return `/${parent === undefined ? source : `${parent}/${source}`}`;
}

function validateCanonicalRoutes(surface, owner, errors) {
  if (!Array.isArray(surface.reactRoutes) || !Array.isArray(surface.reactRouteFragments)) return;
  const expectedRoutes = new Set(
    surface.reactRouteFragments
      .filter((fragment) => typeof fragment === 'string' && fragment.length > 0)
      .map(canonicalRouteForFragment),
  );
  for (const route of surface.reactRoutes) {
    if (typeof route !== 'string' || (!route.startsWith('/') && route !== '*')) {
      errors.push(`${owner}: invalid canonical route ${String(route)}`);
      continue;
    }
    if (surface.reactAliasOf === undefined && !expectedRoutes.has(route)) {
      errors.push(`${owner}: canonical route ${route} has no matching source fragment`);
    }
  }
  if (surface.reactAliasOf === undefined) {
    for (const fragment of surface.reactRouteFragments) {
      if (typeof fragment !== 'string' || fragment.length === 0) {
        errors.push(`${owner}: invalid source route fragment ${String(fragment)}`);
      } else if (!surface.reactRoutes.includes(canonicalRouteForFragment(fragment))) {
        errors.push(`${owner}: source fragment ${String(fragment)} has no canonical route`);
      }
    }
  }
}

function validateDimensions(card, errors) {
  if (!isRecord(card.dimensions)) {
    errors.push('dimensions must be an object');
    return;
  }
  for (const [key, required] of [
    ['themes', REQUIRED_THEMES],
    ['viewports', REQUIRED_VIEWPORTS],
    ['states', REQUIRED_PAGE_STATES],
    ['inputModes', REQUIRED_INPUT_MODES],
  ]) {
    const actual = card.dimensions[key];
    if (!Array.isArray(actual)) {
      errors.push(`dimensions.${key} must be an array`);
      continue;
    }
    for (const value of required) {
      if (!actual.includes(value)) errors.push(`dimensions.${key}: missing ${value}`);
    }
    for (const value of duplicates(actual)) {
      errors.push(`dimensions.${key}: duplicate ${value}`);
    }
  }
}

function validateVisualEnvironment(card, frontendRoot, errors) {
  const environment = card.visualEnvironment;
  if (!isRecord(environment)) {
    errors.push('visualEnvironment must be an object');
    return;
  }
  for (const key of ['browser', 'locale', 'timezone']) {
    if (typeof environment[key] !== 'string' || environment[key].trim().length === 0) {
      errors.push(`visualEnvironment.${key} must be a non-empty string`);
    }
  }
  if (
    typeof environment.deviceScaleFactor !== 'number' ||
    !Number.isFinite(environment.deviceScaleFactor) ||
    environment.deviceScaleFactor <= 0
  ) {
    errors.push('visualEnvironment.deviceScaleFactor must be a positive number');
  }
  validatePath(frontendRoot, environment.fontFixture, 'visualEnvironment.fontFixture', errors);
  if (environment.animations !== 'disabled') {
    errors.push('visualEnvironment.animations must equal disabled');
  }
  if (
    typeof environment.frozenTime !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(environment.frozenTime) ||
    Number.isNaN(Date.parse(environment.frozenTime))
  ) {
    errors.push('visualEnvironment.frozenTime must be a valid UTC timestamp');
  }
}

function validateCalibrationRisks(card, errors) {
  if (!Array.isArray(card.calibrationRisks) || card.calibrationRisks.length === 0) {
    errors.push('calibrationRisks must be a non-empty array');
    return;
  }
  const ids = card.calibrationRisks.map((risk) => risk?.id);
  for (const id of duplicates(ids)) errors.push(`duplicate calibration risk ${String(id)}`);
  for (const risk of card.calibrationRisks) {
    if (!isRecord(risk) || typeof risk.id !== 'string' || risk.id.trim().length === 0) {
      errors.push('calibration risk entry must have a non-empty string id');
      continue;
    }
    const owner = `calibration risk ${risk.id}`;
    for (const key of ['blueprint', 'react']) {
      if (typeof risk[key] !== 'string' || risk[key].trim().length === 0) {
        errors.push(`${owner}: ${key} must be a non-empty string`);
      }
    }
    validateEnum(risk.status, RECONCILIATION, owner, 'status', errors);
    if (
      risk.status === 'blocked' &&
      (typeof risk.reason !== 'string' || risk.reason.trim().length === 0)
    ) {
      errors.push(`${owner}: blocked status requires a reason`);
    }
  }
}

function validateStates(surface, owner, errors) {
  if (!isRecord(surface.states)) {
    errors.push(`${owner}: states must be an object`);
    return;
  }
  for (const state of REQUIRED_PAGE_STATES) {
    if (!(state in surface.states)) {
      errors.push(`${owner}: missing state ${state}`);
      continue;
    }
    validateEnum(surface.states[state], STATE_STATUSES, owner, 'state status', errors);
  }
  for (const [state, status] of Object.entries(surface.states)) {
    if (!REQUIRED_PAGE_STATES.includes(state)) {
      validateEnum(status, STATE_STATUSES, owner, 'state status', errors);
    }
    if (
      status === 'not-applicable' &&
      (!isRecord(surface.stateNotes) ||
        typeof surface.stateNotes[state] !== 'string' ||
        surface.stateNotes[state].trim().length === 0)
    ) {
      errors.push(`${owner}: not-applicable state ${state} requires stateNotes.${state}`);
    }
  }
}

function validateInteractions(surface, card, frontendRoot, owner, errors) {
  if (!Array.isArray(surface.interactions) || surface.interactions.length === 0) {
    errors.push(`${owner}: interactions must be a non-empty array`);
    return;
  }
  const ids = surface.interactions.map((interaction) => interaction?.id);
  for (const id of duplicates(ids)) errors.push(`${owner}: duplicate interaction ${String(id)}`);
  for (const interaction of surface.interactions) {
    if (!isRecord(interaction) || typeof interaction.id !== 'string') {
      errors.push(`${owner}: invalid interaction entry`);
      continue;
    }
    const interactionOwner = `${owner} interaction ${interaction.id}`;
    validateEnum(interaction.status, STATE_STATUSES, interactionOwner, 'state status', errors);
    if (!Array.isArray(interaction.inputModes) || interaction.inputModes.length === 0) {
      errors.push(`${interactionOwner}: inputModes must be a non-empty array`);
    } else {
      for (const mode of interaction.inputModes) {
        if (!card.dimensions?.inputModes?.includes(mode)) {
          errors.push(`${interactionOwner}: unknown input mode ${String(mode)}`);
        }
      }
      for (const mode of duplicates(interaction.inputModes)) {
        errors.push(`${interactionOwner}: duplicate input mode ${mode}`);
      }
    }
    if (interaction.status === 'verified') {
      if (!Array.isArray(interaction.evidence) || interaction.evidence.length === 0) {
        errors.push(`${interactionOwner}: verified interaction requires evidence`);
      } else {
        const coveredModes = [];
        for (const evidence of interaction.evidence) {
          if (!isRecord(evidence)) {
            errors.push(`${interactionOwner}: evidence must be an object`);
            continue;
          }
          const { path, testTitle, inputModes } = evidence;
          validatePlaywrightTarget(evidence, frontendRoot, interactionOwner, errors);
          let testSource = null;
          if (typeof path !== 'string' || !path.startsWith('e2e/') || !path.endsWith('.spec.ts')) {
            errors.push(`${interactionOwner}: evidence path must be an e2e spec: ${String(path)}`);
          } else {
            validatePath(frontendRoot, path, interactionOwner, errors);
            const target = resolve(frontendRoot, path);
            if (existsSync(target)) {
              if (typeof testTitle !== 'string' || testTitle.trim().length === 0) {
                errors.push(`${interactionOwner}: evidence testTitle must be a non-empty string`);
              } else {
                testSource = findTestCaseSource(readFileSync(target, 'utf8'), testTitle);
                if (testSource === null) {
                  errors.push(`${interactionOwner}: evidence testTitle not found in ${path}`);
                }
              }
            }
          }
          if (!Array.isArray(inputModes) || inputModes.length === 0) {
            errors.push(`${interactionOwner}: evidence inputModes must be a non-empty array`);
          } else {
            for (const mode of duplicates(inputModes)) {
              errors.push(`${interactionOwner}: evidence has duplicate input mode ${mode}`);
            }
            for (const mode of inputModes) {
              if (!interaction.inputModes.includes(mode)) {
                errors.push(
                  `${interactionOwner}: evidence has undeclared input mode ${String(mode)}`,
                );
              } else if (testSource !== null && testExercisesInputMode(testSource, mode)) {
                coveredModes.push(mode);
              } else if (testSource !== null) {
                errors.push(`${interactionOwner}: test does not exercise input mode ${mode}`);
              }
            }
          }
        }
        for (const mode of interaction.inputModes) {
          if (!coveredModes.includes(mode)) {
            errors.push(`${interactionOwner}: no evidence covers input mode ${mode}`);
          }
        }
      }
    }
    if (
      ['blocked', 'not-applicable'].includes(interaction.status) &&
      (typeof interaction.reason !== 'string' || interaction.reason.trim().length === 0)
    ) {
      errors.push(`${interactionOwner}: ${interaction.status} interaction requires a reason`);
    }
  }
}

function validatePages(card, frontendRoot, routeSource, visualArtifactRegistry, errors) {
  const ownedRoutes = [];
  if (!Array.isArray(card.pages)) {
    errors.push('pages must be an array');
    return ownedRoutes;
  }

  const pageIds = card.pages.map((page) => page?.id);
  for (const id of duplicates(pageIds)) errors.push(`duplicate page id ${String(id)}`);
  for (const id of BLUEPRINT_PAGE_IDS) {
    if (!pageIds.includes(id)) errors.push(`missing blueprint page ${id}`);
  }
  for (const id of pageIds) {
    if (!BLUEPRINT_PAGE_IDS.includes(id)) errors.push(`unknown blueprint page ${String(id)}`);
  }

  const allBlueprintRoutes = [];
  for (const page of card.pages) {
    if (!isRecord(page)) {
      errors.push('page entry must be an object');
      continue;
    }
    const owner = `page ${String(page.id)}`;
    validateEnum(page.strategy, STRATEGIES, owner, 'strategy', errors);
    validateEnum(page.reconciliation, RECONCILIATION, owner, 'reconciliation', errors);

    for (const key of ['blueprintRoutes', 'reactRoutes']) {
      if (!Array.isArray(page[key]) || page[key].length === 0) {
        errors.push(`${owner}: ${key} must be a non-empty array`);
      }
    }
    if (!Array.isArray(page.reactRouteFragments)) {
      errors.push(`${owner}: reactRouteFragments must be an array`);
    } else if (page.reactRouteFragments.length === 0 && typeof page.reactAliasOf !== 'string') {
      errors.push(`${owner}: reactRouteFragments must be non-empty unless reactAliasOf is set`);
    }
    if (Array.isArray(page.blueprintRoutes)) allBlueprintRoutes.push(...page.blueprintRoutes);
    if (Array.isArray(page.reactRouteFragments)) {
      for (const fragment of page.reactRouteFragments) {
        ownedRoutes.push({ fragment, owner });
        const doubleQuoted = `path="${fragment}"`;
        const singleQuoted = `path='${fragment}'`;
        if (!routeSource.includes(doubleQuoted) && !routeSource.includes(singleQuoted)) {
          errors.push(`${owner}: route fragment not found in ${DEFAULT_ROUTE_SOURCE}: ${fragment}`);
        }
      }
    }
    validateCanonicalRoutes(page, owner, errors);

    for (const key of ['components', 'unitTests', 'e2eTests']) {
      if (!Array.isArray(page[key]) || page[key].length === 0) {
        errors.push(`${owner}: ${key} must be a non-empty array`);
        continue;
      }
      for (const path of page[key]) validatePath(frontendRoot, path, owner, errors);
    }

    validateStates(page, owner, errors);
    validateInteractions(page, card, frontendRoot, owner, errors);
    validateVisualEvidence(page, card, frontendRoot, visualArtifactRegistry, owner, errors);
  }

  for (const route of duplicates(allBlueprintRoutes)) {
    errors.push(`duplicate blueprint route ${String(route)}`);
  }
  const pageIdSet = new Set(pageIds);
  const pagesById = new Map(card.pages.map((page) => [page?.id, page]));
  for (const page of card.pages) {
    if (page?.reactAliasOf !== undefined && !pageIdSet.has(page.reactAliasOf)) {
      errors.push(`page ${String(page?.id)}: unknown reactAliasOf ${String(page.reactAliasOf)}`);
    } else if (page?.reactAliasOf !== undefined) {
      const targetRoutes = pagesById.get(page.reactAliasOf)?.reactRoutes ?? [];
      for (const route of page.reactRoutes ?? []) {
        const base = normalizedRoutePath(route);
        if (!targetRoutes.some((target) => normalizedRoutePath(target) === base)) {
          errors.push(
            `page ${String(page.id)}: alias route ${String(route)} does not match ${page.reactAliasOf}`,
          );
        }
      }
    }
    if (!isRecord(page)) continue;
    const aliasContract = REQUIRED_ALIAS_ROUTES.get(page.id);
    if (aliasContract === undefined && page.reactAliasOf !== undefined) {
      errors.push(`page ${String(page.id)}: unexpected React alias`);
    } else if (aliasContract !== undefined) {
      if (page.reactAliasOf !== aliasContract.target) {
        errors.push(
          `page ${page.id}: reactAliasOf must equal ${aliasContract.target}, received ${String(page.reactAliasOf)}`,
        );
      }
      if (
        !Array.isArray(page.reactRoutes) ||
        page.reactRoutes.length !== aliasContract.routes.length ||
        aliasContract.routes.some((route) => !page.reactRoutes.includes(route))
      ) {
        errors.push(`page ${page.id}: alias routes must equal ${aliasContract.routes.join(', ')}`);
      }
    }
  }
  return ownedRoutes;
}

function validateReactExtensions(card, frontendRoot, routeSource, visualArtifactRegistry, errors) {
  if (!Array.isArray(card.reactExtensions)) {
    errors.push('reactExtensions must be an array');
    return [];
  }
  const ownedRoutes = [];
  const ids = card.reactExtensions.map((extension) => extension?.id);
  for (const id of duplicates(ids)) errors.push(`duplicate React extension id ${String(id)}`);

  for (const extension of card.reactExtensions) {
    if (!isRecord(extension)) {
      errors.push('React extension entry must be an object');
      continue;
    }
    const owner = `React extension ${String(extension.id)}`;
    validateEnum(extension.strategy, STRATEGIES, owner, 'strategy', errors);
    validateEnum(extension.reconciliation, RECONCILIATION, owner, 'reconciliation', errors);
    for (const key of [
      'reactRoutes',
      'reactRouteFragments',
      'components',
      'unitTests',
      'e2eTests',
    ]) {
      if (!Array.isArray(extension[key]) || extension[key].length === 0) {
        errors.push(`${owner}: ${key} must be a non-empty array`);
      }
    }
    if (Array.isArray(extension.reactRouteFragments)) {
      for (const fragment of extension.reactRouteFragments) {
        ownedRoutes.push({ fragment, owner });
        const doubleQuoted = `path="${fragment}"`;
        const singleQuoted = `path='${fragment}'`;
        if (!routeSource.includes(doubleQuoted) && !routeSource.includes(singleQuoted)) {
          errors.push(`${owner}: route fragment not found in ${DEFAULT_ROUTE_SOURCE}: ${fragment}`);
        }
      }
    }
    validateCanonicalRoutes(extension, owner, errors);
    for (const key of ['components', 'unitTests', 'e2eTests']) {
      if (Array.isArray(extension[key])) {
        for (const path of extension[key]) validatePath(frontendRoot, path, owner, errors);
      }
    }
    validateStates(extension, owner, errors);
    validateInteractions(extension, card, frontendRoot, owner, errors);
    validateVisualEvidence(extension, card, frontendRoot, visualArtifactRegistry, owner, errors);
  }
  return ownedRoutes;
}

function extractPrototypeRoutes(source) {
  const routeInfoMatch = source.match(/const\s+routeInfo\s*=\s*\{([\s\S]*?)\n\s*\};/u);
  if (routeInfoMatch === null) return [];
  const routes = [...routeInfoMatch[1].matchAll(/^\s+([a-z][a-z0-9-]*):\s*\{/gmu)].map(
    (match) => match[1],
  );
  const authMatch = source.match(
    /\[(["']login["'][^\]]*["']register["'][^\]]*["']code["'])\]\.includes\(state\.route\)/su,
  );
  if (authMatch !== null) routes.push('login', 'register', 'code');
  return [...new Set(routes)].sort();
}

function validatePrototypeInventory(card, routeSource, tokenSource, errors) {
  if (routeSource !== undefined) {
    const actualRoutes = extractPrototypeRoutes(routeSource);
    if (actualRoutes.length === 0) {
      errors.push('prototype inventory: could not extract routes from pinned source');
    } else {
      const declaredRoutes = card.pages?.flatMap((page) => page?.blueprintRoutes ?? []) ?? [];
      for (const route of actualRoutes) {
        if (!declaredRoutes.includes(route)) errors.push(`unmapped prototype route ${route}`);
      }
      for (const route of declaredRoutes) {
        if (!actualRoutes.includes(route)) errors.push(`unknown prototype route ${String(route)}`);
      }
    }
  }
  if (tokenSource !== undefined) {
    const actualTokens = extractCssCustomPropertyNames(tokenSource);
    const declaredTokens = card.tokens?.map((token) => token?.blueprint) ?? [];
    for (const token of actualTokens) {
      if (!declaredTokens.includes(token)) errors.push(`unmapped prototype token ${token}`);
    }
    for (const token of declaredTokens) {
      if (!actualTokens.has(token)) errors.push(`unknown prototype token ${String(token)}`);
    }
  }
}

function extractLegacyRouteRules(source) {
  return [
    ...source.matchAll(/pattern:\s*\/(\^.+?\$)\/,\s*build:\s*\([^)]*\)\s*=>\s*`([^`]+)`/gsu),
  ].map((match) => ({ pattern: match[1], target: match[2] }));
}

function normalizeLegacyTarget(target) {
  return target
    .replace(/\$\{(?:slug|m\[\d+\])\}/gu, ':parameter')
    .replace(/:[A-Za-z][A-Za-z0-9_]*/gu, ':parameter');
}

function routeSegments(route) {
  return route
    .split(/[?#]/u, 1)[0]
    .replace(/^\/+|\/+$/gu, '')
    .split('/');
}

function legacyTargetBelongsToOwner(target, ownerRoutes) {
  const targetSegments = routeSegments(target);
  const restIndex = targetSegments.indexOf(':rest');
  return ownerRoutes.some((route) => {
    const candidate = routeSegments(route);
    const comparedTarget = restIndex === -1 ? targetSegments : targetSegments.slice(0, restIndex);
    if (restIndex === -1 && candidate.length !== comparedTarget.length) return false;
    if (restIndex !== -1 && candidate.length < comparedTarget.length) return false;
    return comparedTarget.every((segment, index) => {
      const candidateSegment = candidate[index];
      return (
        candidateSegment === segment ||
        (segment.startsWith(':') && !candidateSegment.startsWith(':'))
      );
    });
  });
}

function validateLegacyRoutes(card, source, ownersById, errors) {
  if (!Array.isArray(card.legacyRoutes)) {
    errors.push('legacyRoutes must be an array');
    return;
  }
  const patterns = card.legacyRoutes.map((route) => route?.pattern);
  for (const pattern of duplicates(patterns)) {
    errors.push(`duplicate legacy route ${String(pattern)}`);
  }
  const actualRules = extractLegacyRouteRules(source);
  const patternDeclarations = [...source.matchAll(/\{\s*pattern\s*:/gu)].length;
  if (actualRules.length !== patternDeclarations) {
    errors.push(
      `legacy routes: extracted ${actualRules.length} of ${patternDeclarations} pattern declarations`,
    );
  }
  const actualPatterns = actualRules.map((route) => route.pattern);
  for (const pattern of actualPatterns) {
    if (!patterns.includes(pattern)) errors.push(`unmapped legacy route ${pattern}`);
  }
  for (const route of card.legacyRoutes) {
    if (!isRecord(route)) {
      errors.push('legacy route entry must be an object');
      continue;
    }
    if (!actualPatterns.includes(route.pattern)) {
      errors.push(`unknown legacy route ${String(route.pattern)}`);
    }
    if (!ownersById.has(route.owner)) {
      errors.push(`legacy route ${String(route.pattern)}: unknown owner ${String(route.owner)}`);
    }
    if (typeof route.target !== 'string' || route.target.length === 0) {
      errors.push(`legacy route ${String(route.pattern)}: target must be a non-empty string`);
    } else {
      const ownerRoutes = ownersById.get(route.owner)?.reactRoutes ?? [];
      if (ownersById.has(route.owner) && !legacyTargetBelongsToOwner(route.target, ownerRoutes)) {
        errors.push(
          `legacy route ${String(route.pattern)}: target is not owned by ${String(route.owner)}`,
        );
      }
      const actual = actualRules.find((candidate) => candidate.pattern === route.pattern);
      if (
        actual !== undefined &&
        normalizeLegacyTarget(actual.target) !== normalizeLegacyTarget(route.target)
      ) {
        errors.push(`legacy route ${String(route.pattern)}: target does not match route builder`);
      }
    }
  }
}

function validateRouteOwnership(routeSource, ownedRoutes, errors) {
  const byFragment = new Map();
  for (const { fragment, owner } of ownedRoutes) {
    if (!byFragment.has(fragment)) byFragment.set(fragment, []);
    byFragment.get(fragment).push(owner);
  }
  for (const [fragment, owners] of byFragment) {
    if (owners.length > 1) {
      errors.push(`React route ${fragment} has multiple owners: ${owners.join(', ')}`);
    }
  }
  const actualRoutes = new Set(
    [...routeSource.matchAll(/<Route\s[^>]*?path=["']([^"']+)["']/gsu)].map((match) => match[1]),
  );
  const pathDeclarations = [...routeSource.matchAll(/\bpath\s*=/gu)].length;
  const literalRouteDeclarations = [
    ...routeSource.matchAll(/<Route\s[^>]*?path=["']([^"']+)["']/gsu),
  ].length;
  if (pathDeclarations !== literalRouteDeclarations) {
    errors.push(
      `routes: extracted ${literalRouteDeclarations} of ${pathDeclarations} path declarations`,
    );
  }
  for (const route of actualRoutes) {
    if (!byFragment.has(route)) errors.push(`unmapped React route ${route}`);
  }
}

function validateComponents(card, frontendRoot, errors) {
  if (!Array.isArray(card.components) || card.components.length === 0) {
    errors.push('components must be a non-empty array');
    return;
  }
  const ids = card.components.map((component) => component?.id);
  for (const id of duplicates(ids)) errors.push(`duplicate component id ${String(id)}`);
  for (const id of REQUIRED_COMPONENT_IDS) {
    if (!ids.includes(id)) errors.push(`missing component model ${id}`);
  }
  for (const id of ids) {
    if (!REQUIRED_COMPONENT_IDS.includes(id)) {
      errors.push(`unknown component model ${String(id)}`);
    }
  }
  for (const component of card.components) {
    if (!isRecord(component)) {
      errors.push('component entry must be an object');
      continue;
    }
    const owner = `component ${String(component.id)}`;
    validateEnum(component.strategy, STRATEGIES, owner, 'strategy', errors);
    validateEnum(component.reconciliation, RECONCILIATION, owner, 'reconciliation', errors);
    if (!Array.isArray(component.reactFiles) || component.reactFiles.length === 0) {
      errors.push(`${owner}: reactFiles must be a non-empty array`);
      continue;
    }
    for (const path of component.reactFiles) validatePath(frontendRoot, path, owner, errors);
  }
}

function validateTokens(card, tokenSource, errors) {
  if (!Array.isArray(card.tokens)) {
    errors.push('tokens must be an array');
    return;
  }
  const blueprintTokens = card.tokens.map((token) => token?.blueprint);
  for (const token of duplicates(blueprintTokens)) {
    errors.push(`duplicate blueprint token ${String(token)}`);
  }
  for (const token of BLUEPRINT_TOKEN_IDS) {
    if (!blueprintTokens.includes(token)) errors.push(`missing blueprint token ${token}`);
  }
  for (const token of blueprintTokens) {
    if (!BLUEPRINT_TOKEN_IDS.includes(token)) {
      errors.push(`unknown blueprint token ${String(token)}`);
    }
  }

  const reactTokens = extractCssCustomPropertyNames(tokenSource);
  const destinationUse = new Map();
  for (const token of card.tokens) {
    if (!isRecord(token)) {
      errors.push('token entry must be an object');
      continue;
    }
    const owner = `token ${String(token.blueprint)}`;
    validateEnum(
      token.strategy ?? card.tokenDefaultStrategy,
      STRATEGIES,
      owner,
      'strategy',
      errors,
    );
    validateEnum(token.reconciliation, RECONCILIATION, owner, 'reconciliation', errors);
    if (!Array.isArray(token.react) || token.react.length === 0) {
      errors.push(`${owner}: react must be a non-empty array`);
      continue;
    }
    for (const reactToken of token.react) {
      destinationUse.set(reactToken, (destinationUse.get(reactToken) ?? 0) + 1);
      if (!reactTokens.has(reactToken)) {
        errors.push(`${owner}: unknown React token ${String(reactToken)}`);
      }
    }
  }
  for (const [reactToken, count] of destinationUse) {
    if (count > 3) {
      errors.push(`collapsed token mapping: ${reactToken} receives ${count} blueprint tokens`);
    }
  }
}

function validateBaseline(card, errors) {
  if (!isRecord(card.baseline)) {
    errors.push('baseline must be an object');
    return;
  }
  const baseline = card.baseline;
  if (baseline.issue !== 'MES-142') errors.push('baseline.issue must equal MES-142');
  if (baseline.pullRequest !== 100) errors.push('baseline.pullRequest must equal 100');
  if (!/^[0-9a-f]{40}$/u.test(baseline.revision ?? '')) {
    errors.push('baseline.revision must be a full lowercase commit SHA');
  }
  if (!BASELINE_DISPOSITIONS.has(baseline.disposition)) {
    errors.push(`invalid baseline disposition ${String(baseline.disposition)}`);
  }
  if (!BASELINE_ADOPTIONS.has(baseline.adoption)) {
    errors.push(`invalid baseline adoption ${String(baseline.adoption)}`);
  }
  if (baseline.disposition === 'cancelled' && baseline.adoption === 'authoritative') {
    errors.push('cancelled baseline must be adopted as partial-input or discarded');
  }
  if (baseline.disposition === 'superseded') {
    if (!isRecord(baseline.supersededBy)) {
      errors.push('superseded baseline requires supersededBy');
    } else {
      if (
        typeof baseline.supersededBy.issue !== 'string' ||
        baseline.supersededBy.issue.length === 0
      ) {
        errors.push('baseline.supersededBy.issue must be a non-empty string');
      }
      if (
        !Number.isInteger(baseline.supersededBy.pullRequest) ||
        baseline.supersededBy.pullRequest <= 0
      ) {
        errors.push('baseline.supersededBy.pullRequest must be a positive integer');
      }
      if (!/^[0-9a-f]{40}$/u.test(baseline.supersededBy.revision ?? '')) {
        errors.push('baseline.supersededBy.revision must be a full lowercase commit SHA');
      }
    }
  } else if (baseline.supersededBy !== null) {
    errors.push('baseline.supersededBy must be null unless disposition is superseded');
  }
}

function validateReleasePolicy(card, errors) {
  if (!isRecord(card.releasePolicy)) {
    errors.push('releasePolicy must be an object');
    return;
  }
  if (card.releasePolicy.ownerDecisionRequired !== true) {
    errors.push('releasePolicy.ownerDecisionRequired must equal true');
  }
  if (card.releasePolicy.requiredAuthority !== 'repository-owner') {
    errors.push('releasePolicy.requiredAuthority must equal repository-owner');
  }
  if (card.releasePolicy.source !== 'github-pull-request-comment') {
    errors.push('releasePolicy.source must equal github-pull-request-comment');
  }
  if (card.releasePolicy.binding !== 'head-and-card-digest') {
    errors.push('releasePolicy.binding must equal head-and-card-digest');
  }
  if (card.releaseApproval !== undefined) {
    errors.push('releaseApproval must not be stored in the model card');
  }
  if (card.blueprint !== undefined) {
    errors.push('legacy blueprint field must not be stored in schemaVersion 2');
  }
}

function visualEvidenceCellCount(evidence) {
  if (!isRecord(evidence)) return 0;
  const dimensions = [evidence.viewports, evidence.themes, evidence.states];
  if (dimensions.some((values) => !Array.isArray(values))) return 0;
  return dimensions.reduce((count, values) => count * values.length, 1);
}

function runtimeClaimKey(parts) {
  return sha256(JSON.stringify(parts));
}

export function collectRuntimeEvidenceClaims(card) {
  const claims = [];
  const inspectSurface = (surface, ownerType) => {
    if (!isRecord(surface) || typeof surface.id !== 'string') return;
    for (const interaction of Array.isArray(surface.interactions) ? surface.interactions : []) {
      if (!isRecord(interaction) || interaction.status !== 'verified') continue;
      for (const evidence of Array.isArray(interaction.evidence) ? interaction.evidence : []) {
        if (!isRecord(evidence)) continue;
        const inputModes = Array.isArray(evidence.inputModes) ? evidence.inputModes : [];
        const identity = [
          'interaction',
          ownerType,
          surface.id,
          interaction.id,
          evidence.path,
          evidence.testTitle,
          evidence.config,
          evidence.project,
          inputModes,
        ];
        claims.push({
          key: runtimeClaimKey(identity),
          kind: 'interaction',
          ownerType,
          ownerId: surface.id,
          interactionId: interaction.id,
          spec: evidence.path,
          testTitle: evidence.testTitle,
          config: evidence.config,
          project: evidence.project,
          inputModes,
        });
      }
    }
    for (const evidence of Array.isArray(surface.visualEvidence) ? surface.visualEvidence : []) {
      if (!isRecord(evidence) || evidence.status !== 'verified') continue;
      for (const artifact of Array.isArray(evidence.artifacts) ? evidence.artifacts : []) {
        if (!isRecord(artifact) || !isRecord(artifact.capture)) continue;
        const identity = [
          'visual',
          ownerType,
          surface.id,
          artifact.viewport,
          artifact.theme,
          artifact.state,
          artifact.path,
        ];
        claims.push({
          key: runtimeClaimKey(identity),
          kind: 'visual',
          ownerType,
          ownerId: surface.id,
          viewport: artifact.viewport,
          theme: artifact.theme,
          state: artifact.state,
          path: artifact.path,
          sha256: artifact.sha256,
          comparison: artifact.comparison,
          spec: artifact.capture.spec,
          testTitle: artifact.capture.testTitle,
          config: artifact.capture.config,
          project: artifact.capture.project,
        });
      }
    }
  };
  for (const page of Array.isArray(card.pages) ? card.pages : []) inspectSurface(page, 'page');
  for (const extension of Array.isArray(card.reactExtensions) ? card.reactExtensions : []) {
    inspectSurface(extension, 'extension');
  }
  return claims;
}

export function summarizeUnresolvedEvidence(card) {
  const items = [];
  const counts = {
    reconciliation: 0,
    states: 0,
    interactions: 0,
    visualEvidenceGroups: 0,
    visualEvidenceCells: 0,
    components: 0,
    tokens: 0,
    calibrationRisks: 0,
  };
  const inspectSurface = (surface, owner) => {
    if (['pending', 'blocked'].includes(surface?.reconciliation)) {
      counts.reconciliation += 1;
      items.push(`${owner}.reconciliation=${surface.reconciliation}`);
    }
    for (const [state, status] of Object.entries(surface?.states ?? {})) {
      if (['pending', 'blocked'].includes(status)) {
        counts.states += 1;
        items.push(`${owner}.states.${state}=${status}`);
      }
    }
    for (const interaction of Array.isArray(surface?.interactions) ? surface.interactions : []) {
      if (['pending', 'blocked'].includes(interaction?.status)) {
        counts.interactions += 1;
        items.push(`${owner}.interactions.${String(interaction?.id)}=${interaction.status}`);
      }
    }
    for (const evidence of Array.isArray(surface?.visualEvidence) ? surface.visualEvidence : []) {
      if (evidence?.status !== 'verified' && evidence?.status !== 'not-applicable') {
        counts.visualEvidenceGroups += 1;
        counts.visualEvidenceCells += visualEvidenceCellCount(evidence);
        items.push(`${owner}.visualEvidence=${String(evidence?.status)}`);
      }
    }
  };
  for (const page of Array.isArray(card.pages) ? card.pages : []) {
    inspectSurface(page, `pages.${String(page?.id)}`);
  }
  for (const extension of Array.isArray(card.reactExtensions) ? card.reactExtensions : []) {
    inspectSurface(extension, `reactExtensions.${String(extension?.id)}`);
  }
  for (const component of Array.isArray(card.components) ? card.components : []) {
    if (['pending', 'blocked'].includes(component?.reconciliation)) {
      counts.components += 1;
      items.push(`components.${String(component?.id)}=${component.reconciliation}`);
    }
  }
  for (const token of Array.isArray(card.tokens) ? card.tokens : []) {
    if (['pending', 'blocked'].includes(token?.reconciliation)) {
      counts.tokens += 1;
      items.push(`tokens.${String(token?.blueprint)}=${token.reconciliation}`);
    }
  }
  for (const risk of Array.isArray(card.calibrationRisks) ? card.calibrationRisks : []) {
    if (['pending', 'blocked'].includes(risk?.status)) {
      counts.calibrationRisks += 1;
      items.push(`calibrationRisks.${String(risk?.id)}=${risk.status}`);
    }
  }
  return { counts, items, totalItems: items.length };
}

function validateReleaseApproval(card, approval, context, errors) {
  if (!isRecord(approval) || !isRecord(context)) {
    errors.push(
      `release gate [owner_decision_required]: product-owner approval is required; ${String(card.baseline?.issue)} is ${String(card.baseline?.disposition)}/${String(card.baseline?.adoption)} and does not authorize release`,
    );
    return;
  }
  const required = {
    source: 'github-pull-request-comment',
    repository: context.repository,
    reviewer: context.repositoryOwner,
    authorAssociation: 'OWNER',
    state: 'APPROVED',
    headSha: context.headSha,
    modelCardSha256: context.modelCardSha256,
    baselineRevision: card.baseline?.revision,
  };
  for (const [field, expected] of Object.entries(required)) {
    if (approval[field] !== expected) {
      errors.push(
        `release gate [owner_decision_invalid]: approval ${field} does not match the trusted release context`,
      );
    }
  }
  if (
    typeof approval.decisionUrl !== 'string' ||
    !/^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+#issuecomment-\d+$/u.test(approval.decisionUrl)
  ) {
    errors.push('release gate [owner_decision_invalid]: approval decisionUrl is invalid');
  }
  if (
    typeof approval.decidedAt !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(approval.decidedAt) ||
    Number.isNaN(Date.parse(approval.decidedAt))
  ) {
    errors.push('release gate [owner_decision_invalid]: approval decidedAt is invalid');
  }
}

function validateRuntimeEvidence(card, evidenceRun, context, errors) {
  const claims = collectRuntimeEvidenceClaims(card);
  if (claims.length === 0) return;
  if (!isRecord(evidenceRun) || !isRecord(context)) {
    errors.push(
      'release gate [runtime_evidence_required]: a current-head Playwright evidence run is required',
    );
    return;
  }
  for (const [field, expected] of [
    ['schemaVersion', 1],
    ['source', 'github-actions-playwright'],
    ['repository', context.repository],
    ['headSha', context.headSha],
    ['modelCardSha256', context.modelCardSha256],
  ]) {
    if (evidenceRun[field] !== expected) {
      errors.push(
        `release gate [runtime_evidence_invalid]: runtime evidence ${field} does not match`,
      );
    }
  }
  if (
    typeof evidenceRun.generatedAt !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(evidenceRun.generatedAt) ||
    Number.isNaN(Date.parse(evidenceRun.generatedAt))
  ) {
    errors.push('release gate [runtime_evidence_invalid]: runtime evidence generatedAt is invalid');
  }
  if (!Array.isArray(evidenceRun.results)) {
    errors.push(
      'release gate [runtime_evidence_invalid]: runtime evidence results must be an array',
    );
    return;
  }
  const results = new Map();
  for (const result of evidenceRun.results) {
    if (!isRecord(result) || typeof result.key !== 'string') {
      errors.push('release gate [runtime_evidence_invalid]: invalid runtime evidence result');
      continue;
    }
    if (results.has(result.key)) {
      errors.push(
        `release gate [runtime_evidence_invalid]: duplicate runtime result ${result.key}`,
      );
    }
    results.set(result.key, result);
  }
  const claimKeys = new Set(claims.map((claim) => claim.key));
  for (const resultKey of results.keys()) {
    if (!claimKeys.has(resultKey)) {
      errors.push(
        `release gate [runtime_evidence_invalid]: unexpected runtime result ${resultKey}`,
      );
    }
  }
  for (const claim of claims) {
    const result = results.get(claim.key);
    if (result === undefined) {
      errors.push(`release gate [runtime_evidence_invalid]: missing runtime result ${claim.key}`);
      continue;
    }
    if (result.status !== 'passed') {
      errors.push(`release gate [runtime_evidence_invalid]: runtime evidence result is not passed`);
    }
    if (claim.kind === 'interaction') {
      const executed = Array.isArray(result.executedInputModes) ? result.executedInputModes : [];
      for (const mode of claim.inputModes) {
        if (!executed.includes(mode)) {
          errors.push(
            `release gate [runtime_evidence_invalid]: runtime interaction did not execute ${mode}`,
          );
        }
      }
    } else {
      if (result.screenshotProduced !== true) {
        errors.push(
          'release gate [runtime_evidence_invalid]: runtime visual did not produce a screenshot',
        );
      }
      for (const [field, expected] of [
        ['artifactSha256', claim.sha256],
        ['baselineSha256', claim.comparison?.baselineSha256],
        ['totalPixels', claim.comparison?.totalPixels],
        ['diffPixels', claim.comparison?.diffPixels],
      ]) {
        if (result[field] !== expected) {
          errors.push(
            `release gate [runtime_evidence_invalid]: runtime visual ${field} does not match`,
          );
        }
      }
    }
  }
}

function validateReleaseGate(card, options, errors) {
  validateReleaseApproval(card, options.releaseApproval, options.releaseContext, errors);
  validateRuntimeEvidence(card, options.evidenceRun, options.releaseContext, errors);
  const { counts, items, totalItems } = summarizeUnresolvedEvidence(card);
  if (totalItems > 0) {
    const sample = items.slice(0, 12).join(', ');
    const suffix = totalItems > 12 ? ` … and ${totalItems - 12} more` : '';
    const breakdown = [
      `reconciliation=${counts.reconciliation}`,
      `states=${counts.states}`,
      `interactions=${counts.interactions}`,
      `visualEvidence=${counts.visualEvidenceGroups} group(s)/${counts.visualEvidenceCells} cell(s)`,
      `components=${counts.components}`,
      `tokens=${counts.tokens}`,
      `calibrationRisks=${counts.calibrationRisks}`,
    ].join(', ');
    errors.push(
      `release gate: ${totalItems} unresolved item(s) [${breakdown}]: ${sample}${suffix}`,
    );
  }
}

export function validateModelCard(card, options = {}) {
  const errors = [];
  const frontendRoot = resolve(options.frontendRoot ?? new URL('..', import.meta.url).pathname);
  const mode = options.mode ?? 'audit';
  if (!isRecord(card)) return ['model card must be an object'];
  if (!['audit', 'release'].includes(mode)) errors.push(`invalid validation mode ${String(mode)}`);
  if (card.schemaVersion !== 2) errors.push('schemaVersion must equal 2');
  if (card.issue !== 'MES-108') errors.push('issue must equal MES-108');
  validateBaseline(card, errors);
  validateReleasePolicy(card, errors);

  validateDimensions(card, errors);
  validateVisualEnvironment(card, frontendRoot, errors);
  validateCalibrationRisks(card, errors);
  const routeSource = readSources(
    frontendRoot,
    options.routeSources ?? [DEFAULT_ROUTE_SOURCE],
    errors,
    'routes',
  );
  const legacyRouteSource = readSources(
    frontendRoot,
    options.legacyRouteSources ?? [DEFAULT_LEGACY_ROUTE_SOURCE],
    errors,
    'legacy routes',
  );
  const tokenSource = readSources(
    frontendRoot,
    options.tokenSources ?? DEFAULT_TOKEN_SOURCES,
    errors,
    'tokens',
  );
  const visualArtifactRegistry = {
    path: new Map(),
    sha256: new Map(),
    baselinePath: new Map(),
    baselineSha256: new Map(),
  };
  const ownedRoutes = validatePages(
    card,
    frontendRoot,
    routeSource,
    visualArtifactRegistry,
    errors,
  );
  ownedRoutes.push(
    ...validateReactExtensions(card, frontendRoot, routeSource, visualArtifactRegistry, errors),
  );
  validateRouteOwnership(routeSource, ownedRoutes, errors);
  const ownersById = new Map([
    ...(card.pages ?? []).map((page) => [page?.id, page]),
    ...(card.reactExtensions ?? []).map((extension) => [extension?.id, extension]),
  ]);
  validateLegacyRoutes(card, legacyRouteSource, ownersById, errors);
  validateComponents(card, frontendRoot, errors);
  validateTokens(card, tokenSource, errors);
  validatePrototypeInventory(
    card,
    options.prototypeRouteSource,
    options.prototypeTokenSource,
    errors,
  );
  if (mode === 'release') validateReleaseGate(card, options, errors);
  return errors;
}

function escapeCell(value) {
  return String(value ?? '')
    .replaceAll('|', '\\|')
    .replaceAll('\n', ' ')
    .trim();
}

function codeList(values) {
  return values.map((value) => `\`${escapeCell(value)}\``).join('<br>');
}

function stateSummary(states) {
  return Object.entries(states)
    .map(([state, status]) => `${escapeCell(state)}=${escapeCell(status)}`)
    .join('<br>');
}

function visualSummary(surface) {
  const counts = new Map();
  for (const evidence of surface.visualEvidence ?? []) {
    const count =
      (evidence.viewports?.length ?? 0) *
      (evidence.themes?.length ?? 0) *
      (evidence.states?.length ?? 0);
    counts.set(evidence.status, (counts.get(evidence.status) ?? 0) + count);
  }
  return [...counts.entries()]
    .map(([status, count]) => `${escapeCell(status)}=${count}`)
    .join('<br>');
}

function routeSummary(surface) {
  const routes = codeList(surface.reactRoutes ?? []);
  return surface.reactAliasOf === undefined
    ? routes
    : `${routes}<br>alias → \`${escapeCell(surface.reactAliasOf)}\``;
}

function interactionSummary(surface) {
  return (surface.interactions ?? [])
    .map(
      (interaction) =>
        `\`${escapeCell(interaction.id)}\` [${(interaction.inputModes ?? []).map(escapeCell).join(', ')}]=${escapeCell(interaction.status)}`,
    )
    .join('<br>');
}

export function renderModelCardMarkdown(card) {
  const lines = [
    '<!-- prettier-ignore-start -->',
    '',
    '# MES-108 React 迁移模型卡',
    '',
    '> 本文件由 `frontend/model-card/mes108-react-migration.json` 生成，请勿手工编辑。',
    '> 机器校验会核对映射、路径、令牌和 PNG 完整性/尺寸；release 作业还会在精确 PR head 上运行 Playwright，并重算实际操作、截图摘要与 RGBA 像素差异。产品视觉取舍仍须人工验收。',
    '',
    '## 基线与门禁',
    '',
    `- 固定设计输入：${escapeCell(card.baseline.issue)} / PR #${escapeCell(card.baseline.pullRequest)} @ \`${escapeCell(card.baseline.revision)}\``,
    `- 输入生命周期：**${escapeCell(card.baseline.disposition)}**；采用方式：**${escapeCell(card.baseline.adoption)}**`,
    '- Release 批准：**尚未确认**；批准不写入模型卡，必须来自当前 PR 上仓库 owner 的外部决策评论，并绑定 head、模型卡摘要与固定输入 revision。',
    `- 主题：${codeList(card.dimensions.themes)}`,
    `- 固定视口：${codeList(card.dimensions.viewports)}`,
    `- 必填状态：${codeList(card.dimensions.states)}`,
    `- 输入方式：${codeList(card.dimensions.inputModes ?? [])}`,
    `- 固定环境：${escapeCell(card.visualEnvironment?.browser)} / ${escapeCell(card.visualEnvironment?.locale)} / ${escapeCell(card.visualEnvironment?.timezone)} / DPR ${escapeCell(card.visualEnvironment?.deviceScaleFactor)} / ${escapeCell(card.visualEnvironment?.fontFixture)}`,
    '',
    '静态输入被取消或部分采用不等于 release 批准；外部 owner 门禁未通过时，本表只用于迁移与差异追踪，不代表 React 页面已成为最终视觉交付。',
    '',
    '## 页面映射',
    '',
    '| Blueprint page | React route | Strategy | Reconciliation | Components | Unit tests | E2E | States | Interactions | 视觉矩阵 | Notes |',
    '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |',
  ];

  for (const page of card.pages) {
    lines.push(
      `| ${escapeCell(page.label)} (\`${escapeCell(page.id)}\`)<br>${codeList(page.blueprintRoutes)} | ${routeSummary(page)} | ${escapeCell(page.strategy)} | ${escapeCell(page.reconciliation)} | ${codeList(page.components)} | ${codeList(page.unitTests)} | ${codeList(page.e2eTests)} | ${stateSummary(page.states)} | ${interactionSummary(page)} | ${visualSummary(page)} | ${escapeCell(page.notes)} |`,
    );
  }

  lines.push(
    '',
    '## React 扩展页面',
    '',
    '| Extension | React route | Strategy | Reconciliation | States | Interactions | 视觉矩阵 | Notes |',
    '| --- | --- | --- | --- | --- | --- | --- | --- |',
  );
  for (const extension of card.reactExtensions ?? []) {
    lines.push(
      `| ${escapeCell(extension.label)} (\`${escapeCell(extension.id)}\`) | ${routeSummary(extension)} | ${escapeCell(extension.strategy)} | ${escapeCell(extension.reconciliation)} | ${stateSummary(extension.states)} | ${interactionSummary(extension)} | ${visualSummary(extension)} | ${escapeCell(extension.notes)} |`,
    );
  }

  lines.push(
    '',
    '## 旧路由兼容映射',
    '',
    '| Source pattern | Owner | Canonical target |',
    '| --- | --- | --- |',
  );
  for (const route of card.legacyRoutes ?? []) {
    lines.push(
      `| \`${escapeCell(route.pattern)}\` | \`${escapeCell(route.owner)}\` | \`${escapeCell(route.target)}\` |`,
    );
  }

  lines.push(
    '',
    '## 共享组件映射',
    '',
    '| Model | Strategy | Reconciliation | React files |',
    '| --- | --- | --- | --- |',
  );
  for (const component of card.components) {
    lines.push(
      `| \`${escapeCell(component.id)}\` | ${escapeCell(component.strategy)} | ${escapeCell(component.reconciliation)} | ${codeList(component.reactFiles)} |`,
    );
  }

  lines.push(
    '',
    '## 令牌映射',
    '',
    '| Static token | React semantic token | Strategy | Reconciliation |',
    '| --- | --- | --- | --- |',
  );
  for (const token of card.tokens) {
    lines.push(
      `| \`${escapeCell(token.blueprint)}\` | ${codeList(token.react)} | ${escapeCell(token.strategy ?? card.tokenDefaultStrategy)} | ${escapeCell(token.reconciliation)} |`,
    );
  }

  lines.push('', '## 已知校准差异', '');
  for (const risk of card.calibrationRisks ?? []) {
    lines.push(
      `- **${escapeCell(risk.id)}**：${escapeCell(risk.blueprint)} → ${escapeCell(risk.react)}（${escapeCell(risk.status)}）`,
    );
  }

  lines.push(
    '',
    '## 自动与人工边界',
    '',
    '- `node scripts/verify-model-card.mjs --mode audit` 校验原型/React 页面全集、路由兼容表、组件/测试文件、状态与视觉矩阵、令牌引用及本文件生成结果。',
    '- `node scripts/verify-model-card.mjs --mode release` 是最终门禁；缺少绑定当前 head 与模型卡摘要的仓库 owner 批准、存在 `pending` / `blocked` 或缺少真实证据时必须失败。',
    '- Unit/E2E 文件存在不等于测试已通过；交互证据必须绑定专用 config / project 与精确的普通 Playwright 测试，release 作业只采纳本次成功运行实际记录到的 mouse / keyboard / touch API。',
    '- 每个已验证视觉单元必须提交独立基线和唯一、摘要绑定且尺寸正确的实际 PNG；视觉 spec 必须用 MES-108 证据 fixture 的 `mes108Screenshot.capture(path)` 逐 claim 捕获，release 作业先隔离 claimed actual，再要求 Playwright 本次重建、精确报告输出路径与截图返回字节摘要，随后以 `rgba-exact-v1` 逐像素重算差异。机器校验不能代替产品对差异取舍的人工判断。',
    '- `pending` 与 `blocked` 必须保留，禁止为通过门禁而改写为已完成；只有真实验证证据才能推进状态。',
    '',
    '<!-- prettier-ignore-end -->',
    '',
  );
  return lines.join('\n');
}
