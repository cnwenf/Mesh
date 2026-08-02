import { existsSync, readFileSync } from 'node:fs';
import { isAbsolute, relative, resolve } from 'node:path';

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

function validateVisualArtifact(artifact, evidenceCells, frontendRoot, owner, errors) {
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
    !/\.(?:png|webp)$/iu.test(path)
  ) {
    errors.push(
      `${owner}: visual artifact must be an image under e2e/evidence/mes108: ${String(path)}`,
    );
  } else {
    validatePath(frontendRoot, path, owner, errors);
  }
  return cell;
}

function validateVisualEvidence(surface, card, frontendRoot, owner, errors) {
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
          const cell = validateVisualArtifact(artifact, evidenceCells, frontendRoot, owner, errors);
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
          if (typeof path !== 'string' || !path.startsWith('e2e/') || !path.endsWith('.spec.ts')) {
            errors.push(`${interactionOwner}: evidence path must be an e2e spec: ${String(path)}`);
          } else {
            validatePath(frontendRoot, path, interactionOwner, errors);
            const target = resolve(frontendRoot, path);
            if (existsSync(target)) {
              if (typeof testTitle !== 'string' || testTitle.trim().length === 0) {
                errors.push(`${interactionOwner}: evidence testTitle must be a non-empty string`);
              } else if (!readFileSync(target, 'utf8').includes(testTitle)) {
                errors.push(`${interactionOwner}: evidence testTitle not found in ${path}`);
              }
            }
          }
          if (!Array.isArray(inputModes) || inputModes.length === 0) {
            errors.push(`${interactionOwner}: evidence inputModes must be a non-empty array`);
          } else {
            for (const mode of inputModes) {
              if (!interaction.inputModes.includes(mode)) {
                errors.push(
                  `${interactionOwner}: evidence has undeclared input mode ${String(mode)}`,
                );
              } else {
                coveredModes.push(mode);
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

function validatePages(card, frontendRoot, routeSource, errors) {
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
    validateVisualEvidence(page, card, frontendRoot, owner, errors);
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

function validateReactExtensions(card, frontendRoot, routeSource, errors) {
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
    validateVisualEvidence(extension, card, frontendRoot, owner, errors);
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

function validateReleaseGate(card, errors) {
  if (card.blueprint?.confirmed !== true) {
    errors.push('release gate: blueprint confirmation is required');
  }
  const unresolved = [];
  const inspectSurface = (surface, owner) => {
    if (['pending', 'blocked'].includes(surface?.reconciliation)) {
      unresolved.push(`${owner}.reconciliation=${surface.reconciliation}`);
    }
    for (const [state, status] of Object.entries(surface?.states ?? {})) {
      if (['pending', 'blocked'].includes(status))
        unresolved.push(`${owner}.states.${state}=${status}`);
    }
    for (const interaction of surface?.interactions ?? []) {
      if (['pending', 'blocked'].includes(interaction?.status)) {
        unresolved.push(`${owner}.interactions.${String(interaction?.id)}=${interaction.status}`);
      }
    }
    for (const evidence of surface?.visualEvidence ?? []) {
      if (evidence?.status !== 'verified' && evidence?.status !== 'not-applicable') {
        unresolved.push(`${owner}.visualEvidence=${String(evidence?.status)}`);
      }
    }
  };
  for (const page of card.pages ?? []) inspectSurface(page, `pages.${String(page?.id)}`);
  for (const extension of card.reactExtensions ?? []) {
    inspectSurface(extension, `reactExtensions.${String(extension?.id)}`);
  }
  for (const component of card.components ?? []) {
    if (['pending', 'blocked'].includes(component?.reconciliation)) {
      unresolved.push(`components.${String(component?.id)}=${component.reconciliation}`);
    }
  }
  for (const token of card.tokens ?? []) {
    if (['pending', 'blocked'].includes(token?.reconciliation)) {
      unresolved.push(`tokens.${String(token?.blueprint)}=${token.reconciliation}`);
    }
  }
  for (const risk of card.calibrationRisks ?? []) {
    if (['pending', 'blocked'].includes(risk?.status)) {
      unresolved.push(`calibrationRisks.${String(risk?.id)}=${risk.status}`);
    }
  }
  if (unresolved.length > 0) {
    const sample = unresolved.slice(0, 12).join(', ');
    const suffix = unresolved.length > 12 ? ` … and ${unresolved.length - 12} more` : '';
    errors.push(`release gate: ${unresolved.length} unresolved item(s): ${sample}${suffix}`);
  }
}

export function validateModelCard(card, options = {}) {
  const errors = [];
  const frontendRoot = resolve(options.frontendRoot ?? new URL('..', import.meta.url).pathname);
  const mode = options.mode ?? 'audit';
  if (!isRecord(card)) return ['model card must be an object'];
  if (!['audit', 'release'].includes(mode)) errors.push(`invalid validation mode ${String(mode)}`);
  if (card.schemaVersion !== 1) errors.push('schemaVersion must equal 1');
  if (card.issue !== 'MES-108') errors.push('issue must equal MES-108');
  if (!isRecord(card.blueprint)) {
    errors.push('blueprint must be an object');
  } else {
    if (card.blueprint.issue !== 'MES-142') errors.push('blueprint.issue must equal MES-142');
    if (typeof card.blueprint.confirmed !== 'boolean') {
      errors.push('blueprint.confirmed must be a boolean');
    }
    if (!/^[0-9a-f]{40}$/u.test(card.blueprint.revision ?? '')) {
      errors.push('blueprint.revision must be a full lowercase commit SHA');
    }
  }

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
  const ownedRoutes = validatePages(card, frontendRoot, routeSource, errors);
  ownedRoutes.push(...validateReactExtensions(card, frontendRoot, routeSource, errors));
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
  if (mode === 'release') validateReleaseGate(card, errors);
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
  const confirmation = card.blueprint.confirmed ? '已确认' : '尚未确认';
  const lines = [
    '<!-- prettier-ignore-start -->',
    '',
    '# MES-108 React 迁移模型卡',
    '',
    '> 本文件由 `frontend/model-card/mes108-react-migration.json` 生成，请勿手工编辑。',
    '> 机器校验只证明映射、路径、测试与令牌引用完整；像素差异仍须按固定环境人工验收。',
    '',
    '## 基线与门禁',
    '',
    `- 静态原型：${escapeCell(card.blueprint.issue)} @ \`${escapeCell(card.blueprint.revision)}\``,
    `- 用户与验收确认：**${confirmation}**`,
    `- 主题：${codeList(card.dimensions.themes)}`,
    `- 固定视口：${codeList(card.dimensions.viewports)}`,
    `- 必填状态：${codeList(card.dimensions.states)}`,
    `- 输入方式：${codeList(card.dimensions.inputModes ?? [])}`,
    `- 固定环境：${escapeCell(card.visualEnvironment?.browser)} / ${escapeCell(card.visualEnvironment?.locale)} / ${escapeCell(card.visualEnvironment?.timezone)} / DPR ${escapeCell(card.visualEnvironment?.deviceScaleFactor)} / ${escapeCell(card.visualEnvironment?.fontFixture)}`,
    '',
    '确认门禁未通过时，本表只用于迁移与差异追踪，不代表 React 页面已成为最终视觉交付。',
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
    '- `node scripts/verify-model-card.mjs --mode release` 是最终门禁；未确认原型、`pending`、`blocked` 或缺少真实证据时必须失败。',
    '- Unit/E2E 文件存在不等于测试已通过；交付时仍须运行对应命令并记录精确结果。',
    '- 颜色、字号、间距、布局、动效、亮暗主题与响应式的像素一致性必须在固定浏览器、视口、DPR 与字体环境中逐页比对。',
    '- `pending` 与 `blocked` 必须保留，禁止为通过门禁而改写为已完成；只有真实验证证据才能推进状态。',
    '',
    '<!-- prettier-ignore-end -->',
    '',
  );
  return lines.join('\n');
}
