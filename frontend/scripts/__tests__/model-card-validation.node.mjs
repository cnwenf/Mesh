import assert from 'node:assert/strict';
import { appendFileSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  BLUEPRINT_PAGE_IDS,
  BLUEPRINT_TOKEN_IDS,
  REQUIRED_COMPONENT_IDS,
  REQUIRED_PAGE_STATES,
  renderModelCardMarkdown,
  validateModelCard,
} from '../model-card-lib.mjs';
import {
  executeModelCardCli,
  parseArguments,
  readPinnedSource,
  runModelCardProcess,
  verifyModelCard,
} from '../verify-model-card.mjs';

const VISUAL_ARTIFACTS = ['390x844', '1440x900'].flatMap((viewport) =>
  ['light', 'dark'].flatMap((theme) =>
    REQUIRED_PAGE_STATES.map((state) => ({
      viewport,
      theme,
      state,
      path: `e2e/evidence/mes108/${viewport}-${theme}-${state}.png`,
    })),
  ),
);

function writeFixture(root, relativePath, content = 'fixture') {
  const target = join(root, relativePath);
  mkdirSync(join(target, '..'), { recursive: true });
  writeFileSync(target, content, 'utf8');
}

function makePage(id) {
  return {
    id,
    label: `Page ${id}`,
    blueprintRoutes: [id],
    reactRoutes: [`/${id}`],
    reactRouteFragments: [id],
    strategy: 'calibrate',
    reconciliation: 'pending',
    components: [`src/pages/${id}.tsx`],
    unitTests: [`src/pages/__tests__/${id}.test.tsx`],
    e2eTests: ['e2e/model-card.spec.ts'],
    states: Object.fromEntries(REQUIRED_PAGE_STATES.map((state) => [state, 'pending'])),
    interactions: [{ id: 'open', inputModes: ['mouse', 'keyboard'], status: 'pending' }],
    visualEvidence: [
      {
        viewports: ['390x844', '1440x900'],
        themes: ['light', 'dark'],
        states: [...REQUIRED_PAGE_STATES],
        status: 'pending',
      },
    ],
    notes: 'Explicitly tracked.',
  };
}

function makeCard() {
  const card = {
    schemaVersion: 1,
    issue: 'MES-108',
    blueprint: {
      issue: 'MES-142',
      revision: 'a82df9ab382223c125b77635c94f228024384518',
      confirmed: false,
    },
    dimensions: {
      themes: ['light', 'dark'],
      viewports: ['390x844', '1440x900'],
      states: [...REQUIRED_PAGE_STATES],
      inputModes: ['mouse', 'keyboard', 'touch'],
    },
    visualEnvironment: {
      browser: 'Chromium',
      locale: 'zh-CN',
      timezone: 'UTC',
      deviceScaleFactor: 1,
      fontFixture: 'e2e/fixtures/fonts',
      animations: 'disabled',
      frozenTime: '2026-07-25T12:00:00Z',
    },
    calibrationRisks: [
      {
        id: 'shell-width',
        blueprint: '256px',
        react: '240px',
        status: 'pending',
      },
    ],
    pages: BLUEPRINT_PAGE_IDS.map(makePage),
    reactExtensions: [],
    legacyRoutes: [],
    tokenDefaultStrategy: 'calibrate',
    components: REQUIRED_COMPONENT_IDS.map((id) => ({
      id,
      strategy: 'reuse',
      reconciliation: 'calibrated',
      reactFiles: [`src/design/components/${id}.tsx`],
    })),
    tokens: BLUEPRINT_TOKEN_IDS.map((blueprint) => ({
      blueprint,
      react: [`--mapped-${blueprint.slice(2)}`],
      reconciliation: 'calibrated',
    })),
  };
  const myIssues = card.pages.find((page) => page.id === 'my-issues');
  const issues = card.pages.find((page) => page.id === 'issues');
  issues.reactRoutes = ['/w/:workspaceSlug/issues'];
  issues.reactRouteFragments = ['w/:workspaceSlug/issues'];
  myIssues.reactAliasOf = 'issues';
  myIssues.reactRoutes = ['/w/:workspaceSlug/issues?mine=true'];
  myIssues.reactRouteFragments = [];
  const agents = card.pages.find((page) => page.id === 'agents');
  const members = card.pages.find((page) => page.id === 'members');
  members.reactRoutes = ['/w/:workspaceSlug/members'];
  members.reactRouteFragments = ['w/:workspaceSlug/members'];
  agents.reactAliasOf = 'members';
  agents.reactRoutes = ['/w/:workspaceSlug/members?member_type=agent'];
  agents.reactRouteFragments = [];
  return card;
}

function makeRepoFixture(card) {
  const root = mkdtempSync(join(tmpdir(), 'mesh-model-card-'));
  writeFixture(root, 'e2e/fixtures/fonts/fixture.woff2');
  for (const artifact of VISUAL_ARTIFACTS) writeFixture(root, artifact.path);
  for (const page of card.pages) {
    for (const file of [...page.components, ...page.unitTests, ...page.e2eTests]) {
      writeFixture(root, file);
    }
  }
  writeFixture(root, 'e2e/model-card.spec.ts', "test('model card interaction', () => {});");
  for (const component of card.components) {
    for (const file of component.reactFiles) writeFixture(root, file);
  }
  const routeSource = card.pages
    .flatMap((page) => page.reactRouteFragments)
    .map((fragment) => `<Route path="${fragment}" />`)
    .join('\n');
  writeFixture(root, 'src/App.tsx', routeSource);
  writeFixture(root, 'src/workspace/flatRoutes.tsx', 'export const FLAT_ROUTE_RULES = [];');
  writeFixture(
    root,
    'src/design/tokens.css',
    `:root {\n${card.tokens.map((token) => `  ${token.react[0]}: #fff;`).join('\n')}\n}`,
  );
  writeFixture(root, 'src/design/tokens-dark.css', '');
  writeFixture(root, 'src/design/typography.css', '');
  writeFixture(root, 'src/design/base.css', '');
  return root;
}

test('accepts a complete model card whose routes, files, states, and tokens resolve', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);

  assert.deepEqual(validateModelCard(card, { frontendRoot: root }), []);
});

test('fails closed on missing or duplicate blueprint page coverage', () => {
  const card = makeCard();
  card.pages.pop();
  card.pages[1].id = card.pages[0].id;
  const root = makeRepoFixture(card);

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('duplicate page id')));
  assert.ok(errors.some((error) => error.includes('missing blueprint page')));
});

test('reports unresolved React routes, files, tokens, and state dimensions together', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].reactRouteFragments = ['route-that-does-not-exist'];
  card.pages[0].components = ['src/pages/missing.tsx'];
  delete card.pages[0].states.error;
  card.tokens[0].react = ['--token-that-does-not-exist'];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('route fragment')));
  assert.ok(errors.some((error) => error.includes('missing file')));
  assert.ok(errors.some((error) => error.includes('missing state')));
  assert.ok(errors.some((error) => error.includes('unknown React token')));
});

test('rejects invalid strategy and reconciliation values instead of silently accepting drift', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].strategy = 'copy';
  card.pages[0].reconciliation = 'done-ish';
  card.pages[0].states.default = 'looks-good';

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('invalid strategy')));
  assert.ok(errors.some((error) => error.includes('invalid reconciliation')));
  assert.ok(errors.some((error) => error.includes('invalid state status')));
});

test('fails when a React route is not owned by a blueprint page or explicit extension', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  appendFileSync(join(root, 'src/App.tsx'), '\n<Route path="unowned" />\n', 'utf8');

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('unmapped React route unowned')));
});

test('rejects canonical React routes that do not correspond to their source fragments or alias', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].reactRoutes = ['/totally/wrong'];
  card.pages[1].reactAliasOf = card.pages[0].id;
  card.pages[1].reactRouteFragments = [];
  card.pages[1].reactRoutes = ['/also/wrong?mode=alias'];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('canonical route /totally/wrong')));
  assert.ok(
    errors.some((error) => error.includes('source fragment auth-login has no canonical route')),
  );
  assert.ok(errors.some((error) => error.includes('alias route /also/wrong?mode=alias')));
});

test('pins alias targets and query semantics exactly', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  const myIssues = card.pages.find((page) => page.id === 'my-issues');
  myIssues.reactRoutes = ['/w/:workspaceSlug/issues?mine=false'];
  const agents = card.pages.find((page) => page.id === 'agents');
  agents.reactAliasOf = 'agents';
  agents.reactRoutes = ['/w/:workspaceSlug/members?member_type=human'];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('my-issues: alias routes must equal')));
  assert.ok(errors.some((error) => error.includes('agents: reactAliasOf must equal members')));
  assert.ok(errors.some((error) => error.includes('agents: alias routes must equal')));
});

test('requires an explicit theme by viewport by state visual matrix for every page', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].visualEvidence[0].states = ['default'];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('missing visual evidence cell')));
});

test('does not allow visual evidence to bypass applicable cells or reuse non-image artifacts', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].visualEvidence = [
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'not-applicable',
      reason: 'claimed as unnecessary',
    },
  ];
  card.pages[1].visualEvidence = [
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'verified',
      artifacts: [
        {
          viewport: '390x844',
          theme: 'light',
          state: 'default',
          path: 'src/App.tsx',
        },
      ],
    },
  ];
  card.pages[1].visualEvidence[0].artifacts.push(
    null,
    structuredClone(VISUAL_ARTIFACTS[0]),
    { ...structuredClone(VISUAL_ARTIFACTS[1]), path: VISUAL_ARTIFACTS[0].path },
    {
      viewport: 'unknown',
      theme: 'light',
      state: 'default',
      path: 'e2e/evidence/mes108/unknown-light-default.png',
    },
  );
  card.pages[2].states.empty = 'not-applicable';
  card.pages[2].stateNotes = { empty: 'No collection exists.' };
  card.pages[3].visualEvidence = [
    { status: 'pending', viewports: '390x844', themes: ['light'], states: ['default'] },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('not-applicable visual cell')));
  assert.ok(errors.some((error) => error.includes('requires not-applicable visual cell')));
  assert.ok(errors.some((error) => error.includes('visual artifact must be an object')));
  assert.ok(errors.some((error) => error.includes('unknown evidence cell')));
  assert.ok(errors.some((error) => error.includes('duplicate visual artifact cell')));
  assert.ok(errors.some((error) => error.includes('duplicate visual artifact path')));
  assert.ok(errors.some((error) => error.includes('one unique artifact per visual cell')));
  assert.ok(errors.some((error) => error.includes('must be an image under e2e/evidence')));
});

test('rejects a collapsed token map that hides distinct semantics behind one destination', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  for (const token of card.tokens) token.react = ['--mapped-shell'];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('collapsed token mapping')));
});

test('checks the pinned prototype route and token inventories instead of trusting hardcoded ids', () => {
  const card = makeCard();
  const prototypeRoutesByPage = {
    'auth-login': ['login'],
    'auth-register': ['register'],
    'auth-code': ['code'],
    'my-issues': ['my'],
    'issue-detail': ['issue'],
    'project-detail': ['project'],
    'agent-detail': ['agent'],
    'skill-detail': ['skill'],
    autopilots: ['autopilot', 'automations'],
    analytics: ['usage', 'analytics'],
    'state-gallery': ['states'],
  };
  for (const page of card.pages) {
    page.blueprintRoutes = prototypeRoutesByPage[page.id] ?? [page.id];
  }
  const root = makeRepoFixture(card);
  const routeInfo = card.pages
    .flatMap((page) => page.blueprintRoutes)
    .filter((route) => !['login', 'register', 'code'].includes(route))
    .map((route) => `    ${route}: { label: '${route}' },`)
    .join('\n');
  const prototypeRouteSource = `
    const routeInfo = {\n${routeInfo}\n    };
    if (["login", "register", "code"].includes(state.route)) {}
  `;
  const prototypeTokenSource = `:root {\n${BLUEPRINT_TOKEN_IDS.map((token) => `  ${token}: value;`).join('\n')}\n}`;

  assert.deepEqual(
    validateModelCard(card, { frontendRoot: root, prototypeRouteSource, prototypeTokenSource }),
    [],
  );

  card.pages[0].blueprintRoutes = ['invented'];
  const errors = validateModelCard(card, {
    frontendRoot: root,
    prototypeRouteSource,
    prototypeTokenSource,
  });
  assert.ok(errors.some((error) => error.includes('unmapped prototype route login')));
  assert.ok(errors.some((error) => error.includes('unknown prototype route invented')));
});

test('fails when a legacy compatibility route has no declared owner', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  writeFixture(
    root,
    'src/workspace/flatRoutes.tsx',
    'export const FLAT_ROUTE_RULES = [{ pattern: /^\\/legacy$/, build: (_m, slug) => `/w/${slug}/legacy` }];',
  );

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('unmapped legacy route ^\\/legacy$')));
});

test('checks each documented legacy target against the real route builder', () => {
  const card = makeCard();
  card.legacyRoutes = [
    {
      pattern: '^\\/legacy\\/([^/]+)$',
      owner: card.pages[0].id,
      target: '/totally/wrong/:value',
    },
  ];
  const root = makeRepoFixture(card);
  writeFixture(
    root,
    'src/workspace/flatRoutes.tsx',
    'export const FLAT_ROUTE_RULES = [{ pattern: /^\\/legacy\\/([^/]+)$/, build: (m, slug) => `/w/${slug}/legacy/${m[1]}` }];',
  );

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('target does not match route builder')));
});

test('binds each legacy target to its declared owner with named parameters intact', () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const card = JSON.parse(
    readFileSync(join(frontendRoot, 'model-card/mes108-react-migration.json'), 'utf8'),
  );
  card.legacyRoutes[0].owner = 'settings';
  const squadTask = card.legacyRoutes.find((route) => route.pattern.includes('tasks'));
  squadTask.target = '/w/:workspaceSlug/squads/:taskId/tasks/:squadId';

  const errors = validateModelCard(card, { frontendRoot });
  assert.ok(errors.some((error) => error.includes('target is not owned by settings')));
  assert.ok(errors.some((error) => error.includes('target is not owned by squads')));
});

test('requires every shared component model exactly once', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.components = card.components.filter((component) => component.id !== 'button');
  card.components.push({
    id: 'invented-component',
    strategy: 'add',
    reconciliation: 'new',
    reactFiles: ['src/design/components/Button.tsx'],
  });

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('missing component model button')));
  assert.ok(errors.some((error) => error.includes('unknown component model invented-component')));
  assert.ok(REQUIRED_COMPONENT_IDS.includes('toast'));
});

test('release mode fails closed while the prototype or any evidence remains pending', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);

  const errors = validateModelCard(card, { frontendRoot: root, mode: 'release' });
  assert.ok(errors.some((error) => error.includes('blueprint confirmation')));
  assert.ok(errors.some((error) => error.includes('release gate')));
});

test('requires a complete fixed visual environment and well-formed calibration risks', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.visualEnvironment.browser = '';
  card.visualEnvironment.deviceScaleFactor = 0;
  card.visualEnvironment.fontFixture = '../outside';
  card.visualEnvironment.animations = 'enabled';
  card.visualEnvironment.frozenTime = 'not-a-time';
  card.calibrationRisks.push({
    id: 'shell-width',
    blueprint: '',
    react: 'value',
    status: 'done-ish',
  });

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'visualEnvironment.browser',
    'visualEnvironment.deviceScaleFactor',
    'visualEnvironment.fontFixture',
    'visualEnvironment.animations',
    'visualEnvironment.frozenTime',
    'duplicate calibration risk shell-width',
    'calibration risk shell-width: blueprint must be a non-empty string',
    'calibration risk shell-width: invalid status',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('release mode fails while a known calibration risk remains unresolved', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.blueprint.confirmed = true;
  for (const page of card.pages) {
    page.reconciliation = 'calibrated';
    for (const state of Object.keys(page.states)) page.states[state] = 'verified';
    for (const interaction of page.interactions) {
      interaction.status = 'verified';
      interaction.evidence = [
        {
          path: 'e2e/model-card.spec.ts',
          testTitle: 'model card interaction',
          inputModes: [...interaction.inputModes],
        },
      ];
    }
    page.visualEvidence = [
      {
        viewports: ['390x844', '1440x900'],
        themes: ['light', 'dark'],
        states: [...REQUIRED_PAGE_STATES],
        status: 'verified',
        artifacts: structuredClone(VISUAL_ARTIFACTS),
      },
    ];
  }

  const errors = validateModelCard(card, { frontendRoot: root, mode: 'release' });
  assert.ok(errors.some((error) => error.includes('calibrationRisks.shell-width=pending')));
});

test('verified interaction evidence must bind e2e test titles to every input mode', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].interactions[0] = {
    id: 'open',
    inputModes: ['mouse', 'keyboard'],
    status: 'verified',
    evidence: [
      'e2e/model-card.spec.ts',
      { path: 'src/App.tsx', testTitle: 'Route', inputModes: ['mouse'] },
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'missing title',
        inputModes: ['gamepad'],
      },
      {
        path: 'e2e/missing-interaction.spec.ts',
        testTitle: 'model card interaction',
        inputModes: ['mouse'],
      },
    ],
  };

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'evidence must be an object',
    'evidence path must be an e2e spec',
    'evidence testTitle not found',
    'evidence has undeclared input mode gamepad',
    'missing file',
    'no evidence covers input mode keyboard',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('accepts and renders the repository model card with extensions and legacy routes', () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const card = JSON.parse(
    readFileSync(join(frontendRoot, 'model-card/mes108-react-migration.json'), 'utf8'),
  );

  assert.deepEqual(validateModelCard(card, { frontendRoot }), []);
  const markdown = renderModelCardMarkdown(card);
  assert.match(markdown, /React 扩展页面/);
  assert.match(markdown, /旧路由兼容映射/);
  assert.match(markdown, /已知校准差异/);
  assert.match(markdown, /select-workspace-and-open-onboarding/);
  assert.match(markdown, /Chromium/);

  const releaseErrors = validateModelCard(card, { frontendRoot, mode: 'release' });
  assert.ok(releaseErrors.some((error) => /^release gate: \d+ unresolved item/u.test(error)));
});

test('aggregates malformed structure, unsafe paths, and invalid evidence errors', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.dimensions.inputModes = ['mouse', 'mouse'];
  card.pages[0].components = ['', '../escape.tsx', '/absolute.tsx'];
  card.pages[0].states.empty = 'not-applicable';
  card.pages[0].interactions = [
    { id: 'blocked', inputModes: ['gamepad', 'gamepad'], status: 'blocked' },
    { id: 'broken', inputModes: [], status: 'verified' },
  ];
  card.pages[0].visualEvidence = [
    null,
    { status: 'blocked', viewports: [], themes: [], states: [] },
    {
      status: 'verified',
      viewports: ['390x844', '390x844'],
      themes: ['light'],
      states: ['default'],
      artifacts: [],
    },
    {
      status: 'pending',
      viewports: ['unknown'],
      themes: ['light'],
      states: ['default'],
    },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'duplicate mouse',
    'non-empty string',
    'stay inside frontend root',
    'not-applicable state empty requires',
    'unknown input mode',
    'duplicate input mode',
    'verified interaction requires evidence',
    'invalid visual evidence entry',
    'blocked visual evidence requires a reason',
    'verified visual evidence requires artifacts',
    'unknown visual evidence cell',
    'missing visual evidence cell',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('reports malformed top-level collections without throwing', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.dimensions = null;
  card.pages = null;
  card.reactExtensions = null;
  card.legacyRoutes = null;
  card.components = [];
  card.tokens = null;

  const errors = validateModelCard(card, { frontendRoot: root, mode: 'invalid' });
  for (const fragment of [
    'invalid validation mode',
    'dimensions must be an object',
    'pages must be an array',
    'reactExtensions must be an array',
    'legacyRoutes must be an array',
    'components must be a non-empty array',
    'tokens must be an array',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('fails closed on malformed top-level identity, dimensions, environment, and risk fields', () => {
  assert.deepEqual(validateModelCard(null), ['model card must be an object']);

  const card = makeCard();
  const root = makeRepoFixture(card);
  card.schemaVersion = 2;
  card.issue = 'MES-other';
  card.blueprint = null;
  card.dimensions.themes = null;
  card.dimensions.viewports = [];
  card.visualEnvironment = null;
  card.calibrationRisks = null;
  let errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'schemaVersion must equal 1',
    'issue must equal MES-108',
    'blueprint must be an object',
    'dimensions.themes must be an array',
    'dimensions.viewports: missing 390x844',
    'visualEnvironment must be an object',
    'calibrationRisks must be a non-empty array',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }

  const invalidBlueprint = makeCard();
  invalidBlueprint.blueprint = { issue: 'wrong', confirmed: 'yes', revision: 'short' };
  invalidBlueprint.calibrationRisks = [null, { id: '', status: 'pending' }];
  invalidBlueprint.calibrationRisks.push({
    id: 'blocked-risk',
    blueprint: 'old',
    react: 'new',
    status: 'blocked',
  });
  errors = validateModelCard(invalidBlueprint, { frontendRoot: root });
  for (const fragment of [
    'blueprint.issue must equal MES-142',
    'blueprint.confirmed must be a boolean',
    'blueprint.revision must be a full lowercase commit SHA',
    'calibration risk entry must have a non-empty string id',
    'calibration risk blocked-risk: blocked status requires a reason',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('aggregates malformed page, extension, legacy, component, and token entries', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0] = null;
  card.pages[1].blueprintRoutes = [];
  card.pages[1].reactRoutes = [];
  card.pages[1].reactRouteFragments = [];
  card.pages[1].components = [];
  card.pages[1].unitTests = [];
  card.pages[1].e2eTests = [];
  card.pages[1].states = null;
  card.pages[1].interactions = [];
  card.pages[2].reactRouteFragments = null;
  card.pages[3].blueprintRoutes = [...card.pages[4].blueprintRoutes];
  card.pages[5].reactAliasOf = 'missing-page';
  card.pages[6].reactRoutes = ['relative-route', null];
  card.pages[7].reactRouteFragments = [...card.pages[6].reactRouteFragments];
  card.reactExtensions = [
    null,
    {
      id: 'broken-extension',
      strategy: 'add',
      reconciliation: 'new',
      reactRoutes: [],
      reactRouteFragments: ['missing-extension-route'],
      components: [],
      unitTests: [],
      e2eTests: [],
      states: null,
      interactions: [],
      visualEvidence: [],
    },
  ];
  writeFixture(
    root,
    'src/workspace/flatRoutes.tsx',
    'export const FLAT_ROUTE_RULES = [{ pattern: /^\\/unparsed$/, build: makeTarget }, { pattern: /^\\/known$/, build: (_m, slug) => `/w/${slug}/known` }];',
  );
  card.legacyRoutes = [
    null,
    { pattern: '^\\/known$', owner: 'missing-owner', target: '' },
    { pattern: '^\\/known$', owner: 'missing-owner', target: '/w/:workspaceSlug/known' },
    { pattern: '^\\/unknown$', owner: card.pages[2].id, target: '/unknown' },
  ];
  card.components[0] = null;
  card.components[1].reactFiles = [];
  card.tokens[0] = null;
  card.tokens[1].blueprint = card.tokens[2].blueprint;
  card.tokens[3].blueprint = '--invented';
  card.tokens[4].react = [];

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'page entry must be an object',
    'blueprintRoutes must be a non-empty array',
    'reactRoutes must be a non-empty array',
    'reactRouteFragments must be non-empty',
    'reactRouteFragments must be an array',
    'components must be a non-empty array',
    'states must be an object',
    'interactions must be a non-empty array',
    'duplicate blueprint route',
    'unknown reactAliasOf missing-page',
    'invalid canonical route relative-route',
    'React extension entry must be an object',
    'React extension broken-extension: route fragment not found',
    'legacy routes: extracted 1 of 2 pattern declarations',
    'duplicate legacy route ^\\/known$',
    'legacy route entry must be an object',
    'unknown legacy route ^\\/unknown$',
    'unknown owner missing-owner',
    'target must be a non-empty string',
    'has multiple owners',
    'component entry must be an object',
    'reactFiles must be a non-empty array',
    'duplicate blueprint token',
    'unknown blueprint token --invented',
    'token entry must be an object',
    'react must be a non-empty array',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('fails closed when source inventories use unparseable route declarations', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  writeFixture(root, 'src/routes-expression.tsx', '<Route path={computedPath} />');
  writeFixture(
    root,
    'src/legacy-expression.tsx',
    'export const rules = [{ pattern: /^\\/legacy$/, build: makeTarget }];',
  );

  const errors = validateModelCard(card, {
    frontendRoot: root,
    routeSources: ['src/routes-expression.tsx'],
    legacyRouteSources: ['src/legacy-expression.tsx'],
    prototypeRouteSource: 'const unrelated = true;',
    prototypeTokenSource: ':root {}',
  });
  assert.ok(errors.some((error) => error.includes('routes: extracted 0 of 1')));
  assert.ok(errors.some((error) => error.includes('legacy routes: extracted 0 of 1')));
  assert.ok(errors.some((error) => error.includes('could not extract routes')));
});

test('reports missing source files and an empty visual evidence collection', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].visualEvidence = [];

  const errors = validateModelCard(card, {
    frontendRoot: root,
    routeSources: ['src/missing-routes.tsx'],
    legacyRouteSources: ['src/missing-legacy-routes.tsx'],
    tokenSources: ['src/missing-tokens.css'],
  });
  assert.ok(errors.some((error) => error.includes('routes: missing source file')));
  assert.ok(errors.some((error) => error.includes('legacy routes: missing source file')));
  assert.ok(errors.some((error) => error.includes('tokens: missing source file')));
  assert.ok(errors.some((error) => error.includes('visualEvidence must be a non-empty array')));
});

test('renders deterministic Markdown with the confirmation gate and page matrix', () => {
  const card = makeCard();
  const markdown = renderModelCardMarkdown(card);

  assert.match(markdown, /MES-108 React 迁移模型卡/);
  assert.match(markdown, /尚未确认/);
  assert.match(markdown, /\| Blueprint page \| React route/);
  assert.match(markdown, /视觉矩阵/);
  assert.match(markdown, new RegExp(BLUEPRINT_PAGE_IDS[0]));
  assert.equal(markdown, renderModelCardMarkdown(card));
});

test('parses model-card CLI modes and rejects unknown arguments', () => {
  assert.deepEqual(parseArguments([]), { mode: 'audit', write: false });
  assert.deepEqual(parseArguments(['--write', '--mode', 'release']), {
    mode: 'release',
    write: true,
  });
  assert.throws(() => parseArguments(['--unknown']), /unknown argument/u);
  assert.throws(() => parseArguments(['--mode', 'invalid']), /must be audit or release/u);
});

test('reads pinned sources with structured errors', () => {
  let invocation;
  const source = readPinnedSource('abc', 'prototype.js', {
    repositoryRoot: '/repo',
    execute: (...args) => {
      invocation = args;
      return 'source';
    },
  });
  assert.equal(source, 'source');
  assert.deepEqual(invocation[0], 'git');
  assert.deepEqual(invocation[1], ['show', 'abc:prototype.js']);
  assert.equal(invocation[2].cwd, '/repo');
  assert.throws(
    () =>
      readPinnedSource('bad', 'prototype.js', {
        execute: () => {
          throw { stderr: 'missing revision' };
        },
      }),
    /cannot read pinned source bad:prototype\.js \(missing revision\)/u,
  );
  assert.throws(
    () =>
      readPinnedSource('bad', 'prototype.js', {
        execute: () => {
          throw 'plain failure';
        },
      }),
    /plain failure/u,
  );
});

test('covers model-card CLI write, success, missing, drift, validation, and catch paths', () => {
  const root = mkdtempSync(join(tmpdir(), 'mesh-model-card-cli-'));
  const manifestPath = join(root, 'model-card.json');
  const documentPath = join(root, 'docs/model-card.md');
  writeFileSync(manifestPath, JSON.stringify({ blueprint: { revision: 'a'.repeat(40) } }), 'utf8');
  const baseOptions = {
    frontendRoot: root,
    repositoryRoot: root,
    manifestPath,
    documentPath,
    readPinned: () => 'pinned source',
    validate: () => [],
    render: () => '# generated\n',
  };

  let result = verifyModelCard(['--write'], baseOptions);
  assert.deepEqual(result, {
    exitCode: 0,
    stdout: 'MES-108 model card document updated.\n',
    stderr: '',
  });
  assert.equal(readFileSync(documentPath, 'utf8'), '# generated\n');

  result = verifyModelCard(['--mode', 'audit'], baseOptions);
  assert.equal(result.exitCode, 0);
  assert.match(result.stdout, /audit validation passed/u);

  result = verifyModelCard([], { ...baseOptions, documentPath: join(root, 'missing.md') });
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /document is missing/u);

  writeFileSync(documentPath, '# stale\n', 'utf8');
  result = verifyModelCard([], baseOptions);
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /document has drifted/u);

  result = verifyModelCard([], {
    ...baseOptions,
    validate: () => ['first error', 'second error'],
  });
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /validation failed \(2\)/u);
  assert.match(result.stderr, /- first error/u);

  result = executeModelCardCli(['--unknown'], baseOptions);
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /unknown argument/u);
  result = executeModelCardCli([], {
    ...baseOptions,
    readFile: () => {
      throw 'read failed';
    },
  });
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /read failed/u);
});

test('covers default CLI dependencies, injected filesystem adapters, and process output', () => {
  const actual = verifyModelCard(['--mode', 'audit']);
  assert.equal(actual.exitCode, 0);

  const cardSource = JSON.stringify({ blueprint: { revision: 'b'.repeat(40) } });
  let written = '';
  let madeDirectory = '';
  const injectedOptions = {
    frontendRoot: '/frontend',
    repositoryRoot: '/repo',
    manifestPath: '/manifest.json',
    documentPath: '/docs/model-card.md',
    readFile: (path) => (path === '/manifest.json' ? cardSource : '# generated\n'),
    fileExists: () => true,
    makeDirectory: (path) => {
      madeDirectory = path;
    },
    writeFile: (_path, value) => {
      written = value;
    },
    readPinned: () => 'source',
    validate: () => [],
    render: () => '# generated\n',
  };
  const updated = verifyModelCard(['--write'], injectedOptions);
  assert.equal(updated.exitCode, 0);
  assert.equal(madeDirectory, '/docs');
  assert.equal(written, '# generated\n');

  const stdout = [];
  const stderr = [];
  const exitCodes = [];
  const processed = runModelCardProcess([], injectedOptions, {
    writeStdout: (value) => stdout.push(value),
    writeStderr: (value) => stderr.push(value),
    setExitCode: (value) => exitCodes.push(value),
  });
  assert.equal(processed.exitCode, 0);
  assert.equal(stdout.length, 1);
  assert.deepEqual(stderr, []);
  assert.deepEqual(exitCodes, [0]);

  runModelCardProcess(['--unknown'], injectedOptions, {
    writeStdout: (value) => stdout.push(value),
    writeStderr: (value) => stderr.push(value),
    setExitCode: (value) => exitCodes.push(value),
  });
  assert.equal(stderr.length, 1);
  assert.deepEqual(exitCodes, [0, 1]);

  const originalStdoutWrite = process.stdout.write;
  const originalStderrWrite = process.stderr.write;
  const originalExitCode = process.exitCode;
  const defaultStdout = [];
  const defaultStderr = [];
  try {
    process.stdout.write = (value) => {
      defaultStdout.push(String(value));
      return true;
    };
    process.stderr.write = (value) => {
      defaultStderr.push(String(value));
      return true;
    };
    process.exitCode = undefined;

    runModelCardProcess([], injectedOptions);
    assert.equal(process.exitCode, 0);
    runModelCardProcess(['--unknown'], injectedOptions);
    assert.equal(process.exitCode, 1);
  } finally {
    process.stdout.write = originalStdoutWrite;
    process.stderr.write = originalStderrWrite;
    process.exitCode = originalExitCode;
  }
  assert.equal(defaultStdout.length, 1);
  assert.equal(defaultStderr.length, 1);
});
