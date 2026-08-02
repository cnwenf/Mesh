import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  appendFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

import {
  MES108_SCREENSHOT_FIXTURE_OPTIONS,
  mes108ScreenshotFixture,
  repositoryRelativePath,
} from '../../e2e/mes108-evidence-fixture.mjs';
import {
  BLUEPRINT_PAGE_IDS,
  BLUEPRINT_TOKEN_IDS,
  collectRuntimeEvidenceClaims,
  REQUIRED_COMPONENT_IDS,
  REQUIRED_PAGE_STATES,
  renderModelCardMarkdown,
  validateModelCard,
} from '../model-card-lib.mjs';
import Mes108PlaywrightReporter, {
  collectApiStepTitles,
  collectScreenshotOutputs,
} from '../mes108-playwright-reporter.mjs';
import {
  defaultPlaywrightRun,
  executeEvidenceCli,
  parseEvidenceArguments,
  runEvidenceProcess,
  runModelCardEvidence,
} from '../run-model-card-evidence.mjs';
import {
  executeModelCardCli,
  parseArguments,
  readPinnedSource,
  runModelCardProcess,
  verifyModelCard,
} from '../verify-model-card.mjs';

const TEST_VISUAL_ENVIRONMENT = {
  browser: 'Chromium',
  locale: 'zh-CN',
  timezone: 'UTC',
  deviceScaleFactor: 1,
  fontFixture: 'e2e/fixtures/fonts',
  animations: 'disabled',
  frozenTime: '2026-07-25T12:00:00Z',
};

function runtimeEnvironment(project = 'phone') {
  return {
    browserName: 'chromium',
    locale: TEST_VISUAL_ENVIRONMENT.locale,
    timezoneId: TEST_VISUAL_ENVIRONMENT.timezone,
    deviceScaleFactor: TEST_VISUAL_ENVIRONMENT.deviceScaleFactor,
    viewport: project === 'phone' ? { width: 390, height: 844 } : { width: 1440, height: 900 },
  };
}

const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

function crc32(content) {
  let value = 0xffffffff;
  for (const byte of content) value = CRC_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8);
  return (value ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])));
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function makePng(width, height, seed) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const rowLength = width * 4 + 1;
  const raw = Buffer.alloc(height * rowLength);
  for (let row = 0; row < height; row += 1) raw[row * rowLength] = 0;
  raw[1] = seed;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function mutatePngChunk(content, targetType, mutate, { repairCrc = true } = {}) {
  const result = Buffer.from(content);
  let offset = 8;
  while (offset < result.length) {
    const length = result.readUInt32BE(offset);
    const typeStart = offset + 4;
    const dataStart = typeStart + 4;
    const dataEnd = dataStart + length;
    const type = result.toString('ascii', typeStart, dataStart);
    if (type === targetType) {
      mutate(result.subarray(dataStart, dataEnd));
      if (repairCrc) {
        result.writeUInt32BE(crc32(result.subarray(typeStart, dataEnd)), dataEnd);
      }
      return result;
    }
    offset = dataEnd + 4;
  }
  throw new Error(`PNG chunk ${targetType} was not found`);
}

const VISUAL_FILES = new Map();
const VISUAL_ENVIRONMENT_SHA256 = createHash('sha256')
  .update(JSON.stringify(TEST_VISUAL_ENVIRONMENT))
  .digest('hex');
let visualSeed = 1;
const VISUAL_ARTIFACTS = ['390x844', '1440x900'].flatMap((viewport) =>
  ['light', 'dark'].flatMap((theme) =>
    REQUIRED_PAGE_STATES.map((state) => {
      const [width, height] = viewport.split('x').map(Number);
      const path = `e2e/evidence/mes108/${viewport}-${theme}-${state}.png`;
      const baselinePath = `e2e/evidence/mes108/baselines/${viewport}-${theme}-${state}.png`;
      const content = makePng(width, height, visualSeed);
      visualSeed += 1;
      const digest = createHash('sha256').update(content).digest('hex');
      VISUAL_FILES.set(path, content);
      VISUAL_FILES.set(baselinePath, content);
      return {
        viewport,
        theme,
        state,
        path,
        sha256: digest,
        capture: {
          runner: 'playwright',
          config: 'playwright.mes108.config.ts',
          project: viewport === '390x844' ? 'phone' : 'wide',
          spec: 'e2e/model-card.spec.ts',
          testTitle: 'model card visual',
          capturedAt: '2026-08-02T12:00:00Z',
          environmentSha256: VISUAL_ENVIRONMENT_SHA256,
        },
        comparison: {
          baselinePath,
          baselineSha256: digest,
          actualSha256: digest,
          algorithm: 'rgba-exact-v1',
          totalPixels: width * height,
          diffPixels: 0,
          threshold: 0,
          status: 'matched',
        },
      };
    }),
  ),
);

function writeFixture(root, relativePath, content = 'fixture') {
  const target = join(root, relativePath);
  mkdirSync(join(target, '..'), { recursive: true });
  if (typeof content === 'string') writeFileSync(target, content, 'utf8');
  else writeFileSync(target, content);
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
    schemaVersion: 2,
    issue: 'MES-108',
    baseline: {
      issue: 'MES-142',
      pullRequest: 100,
      revision: 'a82df9ab382223c125b77635c94f228024384518',
      disposition: 'cancelled',
      adoption: 'partial-input',
      supersededBy: null,
    },
    releasePolicy: {
      ownerDecisionRequired: true,
      requiredAuthority: 'repository-owner',
      source: 'github-pull-request-comment',
      binding: 'head-and-card-digest',
    },
    dimensions: {
      themes: ['light', 'dark'],
      viewports: ['390x844', '1440x900'],
      states: [...REQUIRED_PAGE_STATES],
      inputModes: ['mouse', 'keyboard', 'touch'],
    },
    visualEnvironment: structuredClone(TEST_VISUAL_ENVIRONMENT),
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
  for (const [path, content] of VISUAL_FILES) writeFixture(root, path, content);
  for (const page of card.pages) {
    for (const file of [...page.components, ...page.unitTests, ...page.e2eTests]) {
      writeFixture(root, file);
    }
  }
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `import { expect, test } from './mes108-evidence-fixture.mjs';

    test('model card interaction', async ({ page }) => {
      await page.getByRole('button').click();
      await page.keyboard.press('Enter');
      await page.touchscreen.tap(1, 1);
      // await page.screenshot(); must not count as visual capture provenance.
    });
    test('model card visual', async ({ page, mes108Screenshot }) => {
      await expect(page).toHaveScreenshot('model-card.png');
      ${VISUAL_ARTIFACTS.map(
        (artifact) => `await mes108Screenshot.capture('${artifact.path}');`,
      ).join('\n      ')}
    });`,
  );
  writeFixture(root, 'e2e/mes108-evidence-fixture.mjs', 'export { expect, test };');
  writeFixture(root, 'playwright.mes108.config.ts', 'export default {};');
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

function enableRuntimeEvidence(card) {
  const page = card.pages[0];
  page.interactions[0] = {
    id: 'open',
    inputModes: ['mouse', 'keyboard', 'touch'],
    status: 'verified',
    evidence: [
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'model card interaction',
        config: 'playwright.mes108.config.ts',
        project: 'phone',
        inputModes: ['mouse', 'keyboard', 'touch'],
      },
    ],
  };
  page.visualEvidence = [
    {
      viewports: [VISUAL_ARTIFACTS[0].viewport],
      themes: [VISUAL_ARTIFACTS[0].theme],
      states: [VISUAL_ARTIFACTS[0].state],
      status: 'verified',
      artifacts: [structuredClone(VISUAL_ARTIFACTS[0])],
    },
  ];
  return page;
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
  writeFixture(root, VISUAL_ARTIFACTS[0].path, 'fixture');
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
  assert.ok(errors.some((error) => error.includes('visual artifact sha256')));
  assert.ok(errors.some((error) => error.includes('visual artifact capture must be an object')));
  assert.ok(errors.some((error) => error.includes('invalid PNG content')));
});

test('accepts dimensioned PNG evidence bound to hashes, Playwright capture, and pixel comparison', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].visualEvidence = [
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'verified',
      artifacts: structuredClone(VISUAL_ARTIFACTS),
    },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.deepEqual(
    errors.filter((error) => error.startsWith('page auth-login: visual artifact')),
    [],
  );
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `import { expect, test } from './mes108-evidence-fixture.mjs';

    test('model card visual', async ({ page, mes108Screenshot }) => {
      await expect(page).toHaveScreenshot('model-card.png');
      await mes108Screenshot.capture('${VISUAL_ARTIFACTS[0].path}');
    });`,
  );
  assert.ok(
    validateModelCard(card, { frontendRoot: root }).some((error) =>
      error.includes('does not capture claimed screenshot path'),
    ),
  );
});

test('rejects visual provenance from an unused fixture import and self-reported attachment', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].visualEvidence = [
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'verified',
      artifacts: structuredClone(VISUAL_ARTIFACTS),
    },
  ];
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `import { expect, test as evidenceTest } from './mes108-evidence-fixture.mjs';
    import { test } from '@playwright/test';

    test('model card visual', async ({ page }, testInfo) => {
      await expect(page).toHaveScreenshot('model-card.png');
      await page.screenshot({ path: '${VISUAL_ARTIFACTS[0].path}' });
      await testInfo.attach('mes108-screenshot-outputs', {
        body: Buffer.from('self-reported'),
        contentType: 'application/json',
      });
    });`,
  );

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('must import test and expect from')));
  assert.ok(errors.some((error) => error.includes('does not capture claimed screenshot path')));
});

test('rejects malformed capture metadata and unapproved pixel-difference claims', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  const artifact = structuredClone(VISUAL_ARTIFACTS[0]);
  artifact.capture = {
    runner: 'manual',
    spec: 'src/App.tsx',
    testTitle: 'missing',
    capturedAt: 'yesterday',
    environmentSha256: '0'.repeat(64),
  };
  artifact.comparison = {
    baselinePath: VISUAL_ARTIFACTS[0].comparison.baselinePath,
    baselineSha256: 'bad',
    actualSha256: 'bad',
    algorithm: 'manual',
    totalPixels: 1,
    diffPixels: -1,
    threshold: 2,
    status: 'approved-difference',
    riskId: 'missing',
    reason: '',
  };
  card.pages[0].visualEvidence = [
    {
      viewports: ['390x844'],
      themes: ['light'],
      states: ['default'],
      status: 'verified',
      artifacts: [artifact],
    },
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'pending',
    },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'capture.runner',
    'capture.spec',
    'capture.capturedAt',
    'capture environment digest',
    'comparison.baselineSha256',
    'comparison.actualSha256',
    'comparison.algorithm',
    'comparison.totalPixels',
    'comparison.diffPixels',
    'comparison.threshold',
    'resolved calibration risk',
    'approved visual difference requires a reason',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('rejects corrupt, mis-sized, untraceable, and duplicated visual artifacts', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  const artifacts = VISUAL_ARTIFACTS.slice(0, 6).map((artifact) => structuredClone(artifact));

  const misSizedContent = makePng(391, 844, 201);
  artifacts[0].path = 'e2e/evidence/mes108/mis-sized.png';
  artifacts[0].sha256 = createHash('sha256').update(misSizedContent).digest('hex');
  artifacts[0].comparison.actualSha256 = artifacts[0].sha256;
  artifacts[0].capture.testTitle = 'missing visual test';
  artifacts[0].comparison.baselinePath = 'e2e/evidence/mes108/not-a-baseline.png';
  writeFixture(root, artifacts[0].path, misSizedContent);

  const corruptCrcContent = mutatePngChunk(
    makePng(390, 844, 202),
    'IDAT',
    (data) => {
      data[0] ^= 0xff;
    },
    { repairCrc: false },
  );
  artifacts[1].path = 'e2e/evidence/mes108/corrupt-crc.png';
  artifacts[1].sha256 = createHash('sha256').update(corruptCrcContent).digest('hex');
  artifacts[1].comparison.actualSha256 = artifacts[1].sha256;
  artifacts[1].capture.testTitle = 'model card interaction';
  writeFixture(root, artifacts[1].path, corruptCrcContent);
  writeFixture(root, artifacts[1].comparison.baselinePath, corruptCrcContent);

  const corruptPixelsContent = mutatePngChunk(makePng(390, 844, 203), 'IDAT', (data) => {
    data.fill(0);
  });
  artifacts[2].path = 'e2e/evidence/mes108/corrupt-pixels.png';
  artifacts[2].sha256 = createHash('sha256').update(corruptPixelsContent).digest('hex');
  artifacts[2].comparison.actualSha256 = artifacts[2].sha256;
  artifacts[2].comparison = null;
  writeFixture(root, artifacts[2].path, corruptPixelsContent);

  artifacts[3].comparison.diffPixels = 1;
  artifacts[3].comparison.status = 'invented';

  const misSizedBaselineArtifact = structuredClone(VISUAL_ARTIFACTS[6]);
  const misSizedBaseline = makePng(391, 844, 204);
  writeFixture(root, misSizedBaselineArtifact.comparison.baselinePath, misSizedBaseline);
  misSizedBaselineArtifact.comparison.baselineSha256 = createHash('sha256')
    .update(misSizedBaseline)
    .digest('hex');
  artifacts.push(misSizedBaselineArtifact);

  artifacts[4].path = 'e2e/evidence/mes108/duplicate-bytes.png';
  writeFixture(root, artifacts[4].path, VISUAL_FILES.get(artifacts[5].path));
  artifacts[4].sha256 = artifacts[5].sha256;
  artifacts[4].comparison.actualSha256 = artifacts[4].sha256;

  artifacts[5].comparison.status = 'approved-difference';
  artifacts[5].comparison.riskId = 'shell-width';
  artifacts[5].comparison.reason = 'Accepted after product calibration.';
  const approvedBaseline = makePng(390, 844, 220);
  writeFixture(root, artifacts[5].comparison.baselinePath, approvedBaseline);
  artifacts[5].comparison.baselineSha256 = createHash('sha256')
    .update(approvedBaseline)
    .digest('hex');
  artifacts[5].comparison.diffPixels = 1;
  artifacts[5].comparison.threshold = 1 / (390 * 844);
  card.calibrationRisks[0].status = 'calibrated';

  card.pages[0].visualEvidence = [
    {
      viewports: ['390x844'],
      themes: ['light'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'verified',
      artifacts,
    },
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'pending',
    },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'dimensions do not match 390x844',
    'comparison.baselinePath is invalid',
    'invalid baseline PNG content',
    'baseline dimensions do not match 390x844',
    'capture.testTitle was not found',
    'capture test does not compare a screenshot',
    'capture test does not capture claimed screenshot path',
    'invalid PNG content',
    'comparison must be an object',
    'comparison.diffPixels does not match pixels',
    'comparison.status is invalid',
    'global duplicate visual artifact sha256',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
  assert.ok(!errors.some((error) => error.includes('approved visual difference')));
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
  assert.ok(errors.some((error) => error.includes('product-owner approval')));
  assert.ok(errors.some((error) => error.includes('release gate')));
});

test('does not treat card-local approval or a cancelled baseline as product-owner approval', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.blueprint = { confirmed: true };
  card.releaseApproval = {
    state: 'APPROVED',
    reviewer: 'self-authored',
  };

  const errors = validateModelCard(card, { frontendRoot: root, mode: 'release' });
  assert.ok(errors.some((error) => error.includes('product-owner approval is required')));
  assert.ok(errors.some((error) => error.includes('releaseApproval must not be stored')));
});

test('validates baseline disposition and external release policy independently', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.baseline.disposition = 'cancelled';
  card.baseline.adoption = 'authoritative';
  card.releasePolicy = {
    requiredAuthority: 'programmer',
    source: 'card-field',
    binding: 'none',
  };

  const invalidLifecycle = structuredClone(card);
  invalidLifecycle.baseline.disposition = 'unknown';
  invalidLifecycle.baseline.adoption = 'partial-input';
  const errors = [
    ...validateModelCard(card, { frontendRoot: root }),
    ...validateModelCard(invalidLifecycle, { frontendRoot: root }),
  ];
  for (const fragment of [
    'invalid baseline disposition',
    'cancelled baseline must be adopted as partial-input or discarded',
    'releasePolicy.requiredAuthority must equal repository-owner',
    'releasePolicy.source must equal github-pull-request-comment',
    'releasePolicy.binding must equal head-and-card-digest',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('binds external product-owner approval to repository, head, card digest, and baseline', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  const releaseContext = {
    repository: 'cnwenf/Mesh',
    repositoryOwner: 'cnwenf',
    headSha: 'a'.repeat(40),
    modelCardSha256: 'b'.repeat(64),
  };
  const releaseApproval = {
    source: 'github-pull-request-comment',
    repository: releaseContext.repository,
    reviewer: releaseContext.repositoryOwner,
    authorAssociation: 'OWNER',
    state: 'APPROVED',
    headSha: releaseContext.headSha,
    modelCardSha256: releaseContext.modelCardSha256,
    baselineRevision: card.baseline.revision,
    decisionUrl: 'https://github.com/cnwenf/Mesh/pull/120#issuecomment-1',
    decidedAt: '2026-08-02T12:00:00Z',
  };

  const accepted = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseApproval,
    releaseContext,
  });
  assert.ok(!accepted.some((error) => error.includes('owner_decision')));

  for (const [field, value] of [
    ['reviewer', 'not-the-owner'],
    ['authorAssociation', 'CONTRIBUTOR'],
    ['headSha', 'c'.repeat(40)],
    ['modelCardSha256', 'd'.repeat(64)],
    ['baselineRevision', 'e'.repeat(40)],
  ]) {
    const errors = validateModelCard(card, {
      frontendRoot: root,
      mode: 'release',
      releaseContext,
      releaseApproval: { ...releaseApproval, [field]: value },
    });
    assert.ok(
      errors.some((error) => error.includes(`approval ${field} does not match`)),
      field,
    );
  }

  const malformedDecision = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    releaseApproval: { ...releaseApproval, decisionUrl: 'not-a-url', decidedAt: 'not-a-time' },
  });
  assert.ok(malformedDecision.some((error) => error.includes('approval decisionUrl is invalid')));
  assert.ok(malformedDecision.some((error) => error.includes('approval decidedAt is invalid')));
});

test('requires runtime Playwright evidence bound to the release head and computed pixels', () => {
  const card = makeCard();
  enableRuntimeEvidence(card);
  const root = makeRepoFixture(card);
  const releaseContext = {
    repository: 'cnwenf/Mesh',
    repositoryOwner: 'cnwenf',
    headSha: 'a'.repeat(40),
    modelCardSha256: 'b'.repeat(64),
  };

  const claims = collectRuntimeEvidenceClaims(card);
  assert.equal(claims.filter((claim) => claim.kind === 'interaction').length, 1);
  assert.equal(claims.filter((claim) => claim.kind === 'visual').length, 1);

  const missing = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
  });
  assert.ok(missing.some((error) => error.includes('runtime_evidence_required')));

  const evidenceRun = runModelCardEvidence(card, {
    frontendRoot: root,
    repository: releaseContext.repository,
    headSha: releaseContext.headSha,
    modelCardSha256: releaseContext.modelCardSha256,
    now: () => new Date('2026-08-02T13:00:00Z'),
    runPlaywright: (run) => {
      for (const claim of run.claims.filter((candidate) => candidate.kind === 'visual')) {
        writeFixture(root, claim.path, VISUAL_FILES.get(claim.path));
      }
      return {
        schemaVersion: 1,
        status: 'passed',
        tests: [
          {
            spec: run.spec,
            testTitle: run.testTitle,
            project: run.project,
            status: 'passed',
            expectedStatus: 'passed',
            environment: runtimeEnvironment(run.project),
            apiSteps:
              run.testTitle === 'model card visual'
                ? ['expect.toHaveScreenshot', 'page.screenshot']
                : ['locator.click', 'keyboard.press', 'touchscreen.tap'],
            screenshotOutputs: run.claims
              .filter((claim) => claim.kind === 'visual')
              .map((claim) => ({
                path: claim.path,
                sha256: createHash('sha256').update(VISUAL_FILES.get(claim.path)).digest('hex'),
              })),
          },
        ],
      };
    },
  });
  assert.equal(evidenceRun.results.length, claims.length);

  const accepted = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    evidenceRun,
  });
  assert.ok(!accepted.some((error) => error.includes('runtime_evidence')));

  const tampered = structuredClone(evidenceRun);
  tampered.results[0].status = 'skipped';
  tampered.headSha = 'c'.repeat(40);
  const rejected = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    evidenceRun: tampered,
  });
  assert.ok(rejected.some((error) => error.includes('runtime evidence headSha does not match')));
  assert.ok(rejected.some((error) => error.includes('runtime evidence result is not passed')));

  const malformedEnvelope = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    evidenceRun: {
      ...evidenceRun,
      schemaVersion: 0,
      source: 'committed-json',
      repository: 'other/repository',
      modelCardSha256: 'd'.repeat(64),
      generatedAt: 'not-a-time',
      results: null,
    },
  });
  for (const fragment of [
    'runtime evidence schemaVersion does not match',
    'runtime evidence source does not match',
    'runtime evidence repository does not match',
    'runtime evidence modelCardSha256 does not match',
    'runtime evidence generatedAt is invalid',
    'runtime evidence results must be an array',
  ]) {
    assert.ok(
      malformedEnvelope.some((error) => error.includes(fragment)),
      fragment,
    );
  }

  const interactionResult = evidenceRun.results.find((result) => result.executedInputModes);
  const visualResult = evidenceRun.results.find((result) => result.screenshotProduced);
  const malformedResults = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    evidenceRun: {
      ...evidenceRun,
      results: [
        null,
        interactionResult,
        { ...interactionResult, status: 'failed', executedInputModes: [] },
        {
          ...visualResult,
          screenshotProduced: false,
          artifactSha256: '0'.repeat(64),
          baselineSha256: '0'.repeat(64),
          totalPixels: 0,
          diffPixels: 1,
        },
        { key: 'unexpected', status: 'passed' },
      ],
    },
  });
  for (const fragment of [
    'invalid runtime evidence result',
    'duplicate runtime result',
    'unexpected runtime result unexpected',
    'runtime evidence result is not passed',
    'runtime interaction did not execute mouse',
    'runtime visual did not produce a screenshot',
    'runtime visual artifactSha256 does not match',
    'runtime visual baselineSha256 does not match',
    'runtime visual totalPixels does not match',
    'runtime visual diffPixels does not match',
  ]) {
    assert.ok(
      malformedResults.some((error) => error.includes(fragment)),
      fragment,
    );
  }

  const missingResult = validateModelCard(card, {
    frontendRoot: root,
    mode: 'release',
    releaseContext,
    evidenceRun: { ...evidenceRun, results: [interactionResult] },
  });
  assert.ok(missingResult.some((error) => error.includes('missing runtime result')));
});

test('runtime evidence runner skips Playwright when the card has no verified claims', () => {
  const card = makeCard();
  let calls = 0;
  const evidenceRun = runModelCardEvidence(card, {
    frontendRoot: makeRepoFixture(card),
    repository: 'cnwenf/Mesh',
    headSha: 'a'.repeat(40),
    modelCardSha256: 'b'.repeat(64),
    now: () => new Date('2026-08-02T13:00:00Z'),
    runPlaywright: () => {
      calls += 1;
      return { tests: [] };
    },
  });
  assert.equal(calls, 0);
  assert.deepEqual(evidenceRun.results, []);
});

test('runtime evidence runner rejects malformed, duplicate, skipped, and untraced test results', () => {
  const card = {
    visualEnvironment: structuredClone(TEST_VISUAL_ENVIRONMENT),
    pages: [
      {
        id: 'runtime-surface',
        interactions: [
          {
            id: 'open',
            status: 'verified',
            evidence: [
              {
                path: 'e2e/runtime.spec.ts',
                testTitle: 'runtime interaction',
                config: 'playwright.mes108.config.ts',
                project: 'phone',
                inputModes: ['mouse'],
              },
            ],
          },
        ],
        visualEvidence: [],
      },
    ],
    reactExtensions: [],
  };
  const options = {
    frontendRoot: mkdtempSync(join(tmpdir(), 'mesh-runtime-report-')),
    repository: 'cnwenf/Mesh',
    headSha: 'a'.repeat(40),
    modelCardSha256: 'b'.repeat(64),
  };
  const exactTest = {
    spec: 'e2e/runtime.spec.ts',
    testTitle: 'runtime interaction',
    project: 'phone',
    status: 'passed',
    expectedStatus: 'passed',
    environment: runtimeEnvironment(),
    apiSteps: ['locator.click'],
    screenshotOutputs: [],
  };

  assert.throws(
    () => runModelCardEvidence(card, { ...options, runPlaywright: () => ({}) }),
    /report is malformed or unsuccessful/u,
  );
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: () => ({ schemaVersion: 1, status: 'passed', tests: [] }),
      }),
    /must contain one exact test/u,
  );
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: () => ({
          schemaVersion: 1,
          status: 'passed',
          tests: [exactTest, { ...exactTest, testTitle: 'other runtime interaction' }],
        }),
      }),
    /must contain one exact test/u,
  );
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: () => ({
          schemaVersion: 1,
          status: 'passed',
          tests: [exactTest, exactTest],
        }),
      }),
    /must contain one exact test/u,
  );
  for (const testResult of [
    { ...exactTest, status: 'skipped' },
    { ...exactTest, expectedStatus: 'failed' },
    { ...exactTest, apiSteps: null },
  ]) {
    assert.throws(
      () =>
        runModelCardEvidence(card, {
          ...options,
          runPlaywright: () => ({
            schemaVersion: 1,
            status: 'passed',
            tests: [testResult],
          }),
        }),
      /skipped, fixed, or unsuccessful/u,
    );
  }
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: () => ({
          schemaVersion: 1,
          status: 'passed',
          tests: [
            {
              ...exactTest,
              environment: { ...runtimeEnvironment(), locale: 'en-US' },
            },
          ],
        }),
      }),
    /environment locale does not match/u,
  );
});

test('runtime visual evidence must be rewritten by a passing screenshot comparison test', () => {
  const root = mkdtempSync(join(tmpdir(), 'mesh-runtime-visual-'));
  const actualPath = 'e2e/evidence/mes108/runtime.png';
  const baselinePath = 'e2e/evidence/mes108/baselines/runtime.png';
  const content = makePng(1, 1, 7);
  writeFixture(root, actualPath, content);
  writeFixture(root, baselinePath, content);
  const card = {
    visualEnvironment: structuredClone(TEST_VISUAL_ENVIRONMENT),
    pages: [
      {
        id: 'runtime-surface',
        interactions: [],
        visualEvidence: [
          {
            status: 'verified',
            artifacts: [
              {
                viewport: '390x844',
                theme: 'light',
                state: 'default',
                path: actualPath,
                sha256: createHash('sha256').update(content).digest('hex'),
                capture: {
                  config: 'playwright.mes108.config.ts',
                  project: 'phone',
                  spec: 'e2e/runtime.spec.ts',
                  testTitle: 'runtime visual',
                },
                comparison: {
                  baselinePath,
                  baselineSha256: createHash('sha256').update(content).digest('hex'),
                  totalPixels: 1,
                  diffPixels: 0,
                },
              },
            ],
          },
        ],
      },
    ],
    reactExtensions: [],
  };
  const options = {
    frontendRoot: root,
    repository: 'cnwenf/Mesh',
    headSha: 'a'.repeat(40),
    modelCardSha256: 'b'.repeat(64),
  };
  const report = (
    run,
    apiSteps,
    {
      rewrite = true,
      screenshotOutputs = [
        { path: actualPath, sha256: createHash('sha256').update(content).digest('hex') },
      ],
    } = {},
  ) => {
    assert.equal(existsSync(join(root, actualPath)), false);
    if (rewrite) {
      writeFixture(root, actualPath, content);
    }
    return {
      schemaVersion: 1,
      status: 'passed',
      tests: [
        {
          spec: run.spec,
          testTitle: run.testTitle,
          project: run.project,
          status: 'passed',
          expectedStatus: 'passed',
          environment: runtimeEnvironment(run.project),
          apiSteps,
          screenshotOutputs,
        },
      ],
    };
  };

  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: (run) =>
          report(run, ['expect.toHaveScreenshot', 'page.screenshot'], { rewrite: false }),
      }),
    /did not produce visual artifact/u,
  );
  assert.deepEqual(readFileSync(join(root, actualPath)), content);
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: (run) => report(run, ['page.screenshot']),
      }),
    /must execute comparison and screenshot API steps/u,
  );
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: (run) =>
          report(run, ['expect.toHaveScreenshot', 'page.screenshot'], {
            screenshotOutputs: [
              {
                path: 'e2e/evidence/mes108/unrelated.png',
                sha256: createHash('sha256').update(content).digest('hex'),
              },
            ],
          }),
      }),
    /screenshot outputs must exactly match/u,
  );
  assert.deepEqual(readFileSync(join(root, actualPath)), content);
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: (run) =>
          report(run, ['expect.toHaveScreenshot', 'page.screenshot'], {
            screenshotOutputs: [{ path: actualPath, sha256: '0'.repeat(64) }],
          }),
      }),
    /reported screenshot sha256 does not match/u,
  );
  writeFixture(root, baselinePath, 'not a PNG');
  assert.throws(
    () =>
      runModelCardEvidence(card, {
        ...options,
        runPlaywright: (run) => report(run, ['expect.toHaveScreenshot', 'page.screenshot']),
      }),
    /cannot compare visual artifact/u,
  );
});

test('default Playwright evidence launcher pins exact config, project, title, and reporter', () => {
  const root = mkdtempSync(join(tmpdir(), 'mesh-runtime-launcher-'));
  for (const path of [
    'playwright.mes108.config.ts',
    'e2e/runtime.spec.ts',
    'node_modules/.bin/playwright',
    'scripts/mes108-playwright-reporter.mjs',
  ]) {
    writeFixture(root, path);
  }
  const run = {
    config: 'playwright.mes108.config.ts',
    project: 'phone',
    spec: 'e2e/runtime.spec.ts',
    testTitle: 'runtime [phone]',
  };
  let invocation;
  const expectedReport = { schemaVersion: 1, status: 'passed', tests: [] };
  const actualReport = defaultPlaywrightRun(run, {
    frontendRoot: root,
    execute: (...args) => {
      invocation = args;
      writeFileSync(args[2].env.MES108_PLAYWRIGHT_REPORT, JSON.stringify(expectedReport), 'utf8');
      return { status: 0 };
    },
  });
  assert.deepEqual(actualReport, expectedReport);
  assert.equal(invocation[0], join(root, 'node_modules/.bin/playwright'));
  assert.deepEqual(invocation[1].slice(0, 7), [
    'test',
    '--config',
    run.config,
    '--project',
    run.project,
    '--grep',
    'runtime \\[phone\\]$',
  ]);
  assert.equal(invocation[2].cwd, root);

  assert.throws(
    () =>
      defaultPlaywrightRun(run, {
        frontendRoot: root,
        execute: () => ({ status: 1, stderr: 'browser failed' }),
      }),
    /browser failed/u,
  );
  assert.throws(
    () =>
      defaultPlaywrightRun(run, {
        frontendRoot: root,
        execute: () => ({ status: null, error: new Error('spawn failed') }),
      }),
    /spawn failed/u,
  );
  assert.throws(
    () => defaultPlaywrightRun(run, { frontendRoot: root, execute: () => ({ status: 0 }) }),
    /reporter did not produce output/u,
  );
  assert.throws(
    () => defaultPlaywrightRun({ ...run, config: '../outside.ts' }, { frontendRoot: root }),
    /must stay inside frontend root/u,
  );
  assert.throws(
    () => defaultPlaywrightRun({ ...run, config: 'missing.ts' }, { frontendRoot: root }),
    /does not resolve/u,
  );
});

test('Playwright reporter records only executed API steps in a runtime file', () => {
  const root = mkdtempSync(join(tmpdir(), 'mesh-model-card-reporter-'));
  const outputPath = join(root, 'report.json');
  const reporter = new Mes108PlaywrightReporter({ outputPath, cwd: root });
  reporter.onTestEnd(
    {
      title: 'model card interaction',
      expectedStatus: 'passed',
      location: { file: join(root, 'e2e/model-card.spec.ts') },
      parent: { project: () => ({ name: 'phone', use: runtimeEnvironment() }) },
    },
    {
      status: 'passed',
      steps: [
        { category: 'hook', title: 'Before Hooks', steps: [] },
        {
          category: 'test.step',
          title: 'operate',
          steps: [
            { category: 'pw:api', title: 'locator.click', steps: [] },
            { category: 'expect', title: 'expect.toHaveScreenshot', steps: [] },
          ],
        },
      ],
      attachments: [
        {
          name: 'mes108-screenshot-outputs',
          contentType: 'application/json',
          body: Buffer.from(
            `[{"path":"e2e/evidence/mes108/model-card.png","sha256":"${'a'.repeat(64)}"}]`,
          ),
        },
      ],
    },
  );
  reporter.onEnd({ status: 'passed' });

  assert.deepEqual(JSON.parse(readFileSync(outputPath, 'utf8')), {
    schemaVersion: 1,
    status: 'passed',
    tests: [
      {
        spec: 'e2e/model-card.spec.ts',
        testTitle: 'model card interaction',
        project: 'phone',
        status: 'passed',
        expectedStatus: 'passed',
        apiSteps: ['locator.click', 'expect.toHaveScreenshot'],
        screenshotOutputs: [{ path: 'e2e/evidence/mes108/model-card.png', sha256: 'a'.repeat(64) }],
        environment: runtimeEnvironment(),
      },
    ],
  });
  assert.deepEqual(collectApiStepTitles(undefined), []);
  assert.deepEqual(collectApiStepTitles([{ category: 'pw:api', title: null }]), []);
  assert.deepEqual(collectScreenshotOutputs(undefined), []);
  for (const attachments of [
    [
      {
        name: 'mes108-screenshot-outputs',
        contentType: 'text/plain',
        body: Buffer.from('[]'),
      },
    ],
    [
      {
        name: 'mes108-screenshot-outputs',
        contentType: 'application/json',
        body: Buffer.from('not json'),
      },
    ],
    [
      {
        name: 'mes108-screenshot-outputs',
        contentType: 'application/json',
        body: Buffer.from(`[{"path":"../outside.png","sha256":"${'a'.repeat(64)}"}]`),
      },
    ],
  ]) {
    assert.throws(() => collectScreenshotOutputs(attachments), /screenshot path manifest/u);
  }

  const reporterWithoutOutput = new Mes108PlaywrightReporter({ outputPath: '', cwd: root });
  reporterWithoutOutput.onTestEnd(
    {
      title: 'no project',
      expectedStatus: 'passed',
      location: { file: join(root, 'e2e/model-card.spec.ts') },
      parent: null,
    },
    { status: 'passed', steps: null, attachments: [] },
  );
  assert.throws(() => reporterWithoutOutput.onEnd({ status: 'passed' }), /REPORT is required/u);

  const defaultOutputPath = join(root, 'default-report.json');
  const previousOutputPath = process.env.MES108_PLAYWRIGHT_REPORT;
  try {
    process.env.MES108_PLAYWRIGHT_REPORT = defaultOutputPath;
    const defaultReporter = new Mes108PlaywrightReporter();
    defaultReporter.onTestEnd(
      {
        title: 'no project result',
        expectedStatus: 'passed',
        location: { file: join(process.cwd(), 'e2e/model-card.spec.ts') },
        parent: { project: () => undefined },
      },
      { status: 'passed', steps: [], attachments: [] },
    );
    defaultReporter.onEnd({ status: 'passed' });
    assert.equal(JSON.parse(readFileSync(defaultOutputPath, 'utf8')).tests[0].project, undefined);
  } finally {
    if (previousOutputPath === undefined) delete process.env.MES108_PLAYWRIGHT_REPORT;
    else process.env.MES108_PLAYWRIGHT_REPORT = previousOutputPath;
  }
});

test('MES-108 evidence fixture records concrete page screenshot outputs', async () => {
  assert.deepEqual(MES108_SCREENSHOT_FIXTURE_OPTIONS, { auto: true });
  const root = mkdtempSync(join(tmpdir(), 'mesh-model-card-fixture-'));
  const output = join(root, 'e2e/evidence/mes108/output.png');
  let attachments = [];
  const page = { screenshot: async () => Buffer.from('png') };
  await mes108ScreenshotFixture(
    { page },
    async (mes108Screenshot) => {
      assert.deepEqual(await mes108Screenshot.capture(output), Buffer.from('png'));
      await mes108Screenshot.capture(join(root, 'e2e/evidence/mes108/a.png'));
    },
    {
      attach: async (name, attachment) => {
        attachments.push({ name, ...attachment });
      },
    },
    { cwd: root },
  );
  assert.equal(repositoryRelativePath(output, root), 'e2e/evidence/mes108/output.png');
  assert.throws(() => repositoryRelativePath(join(root, '../outside.png'), root), /frontend root/u);
  assert.equal(attachments.length, 1);
  assert.deepEqual(JSON.parse(attachments[0].body.toString('utf8')), [
    {
      path: 'e2e/evidence/mes108/a.png',
      sha256: createHash('sha256').update('png').digest('hex'),
    },
    {
      path: 'e2e/evidence/mes108/output.png',
      sha256: createHash('sha256').update('png').digest('hex'),
    },
  ]);
});

test('parses runtime evidence CLI modes and rejects incomplete run bindings', () => {
  assert.deepEqual(parseEvidenceArguments(['--mode', 'plan']), { mode: 'plan' });
  assert.deepEqual(
    parseEvidenceArguments([
      '--mode',
      'run',
      '--output',
      '/tmp/evidence.json',
      '--repository',
      'cnwenf/Mesh',
      '--head',
      'a'.repeat(40),
    ]),
    {
      mode: 'run',
      output: '/tmp/evidence.json',
      repository: 'cnwenf/Mesh',
      headSha: 'a'.repeat(40),
    },
  );
  assert.throws(() => parseEvidenceArguments(['--mode', 'run']), /requires --output/u);
  assert.throws(() => parseEvidenceArguments([]), /mode must be plan or run/u);
  assert.throws(() => parseEvidenceArguments(['--mode']), /requires a value/u);
  assert.throws(
    () =>
      parseEvidenceArguments([
        '--mode',
        'run',
        '--output',
        'evidence.json',
        '--repository',
        'invalid',
        '--head',
        'a'.repeat(40),
      ]),
    /requires --repository owner\/name/u,
  );
  assert.throws(
    () =>
      parseEvidenceArguments([
        '--mode',
        'run',
        '--output',
        'evidence.json',
        '--repository',
        'cnwenf/Mesh',
        '--head',
        'ABC',
      ]),
    /requires --head/u,
  );
  assert.throws(() => parseEvidenceArguments(['--unknown']), /unknown argument/u);
});

test('covers runtime evidence CLI planning, writing, and process error handling', () => {
  const cardSource = JSON.stringify({ pages: [], reactExtensions: [] });
  const baseOptions = {
    cardPath: '/fixture/card.json',
    readFile: () => cardSource,
  };
  assert.deepEqual(executeEvidenceCli(['--mode', 'plan'], baseOptions), {
    exitCode: 0,
    stdout: 'false\n',
    stderr: '',
  });

  let madeDirectory;
  let written;
  const result = executeEvidenceCli(
    [
      '--mode',
      'run',
      '--output',
      '/fixture/results/evidence.json',
      '--repository',
      'cnwenf/Mesh',
      '--head',
      'a'.repeat(40),
    ],
    {
      ...baseOptions,
      makeDirectory: (path) => {
        madeDirectory = path;
      },
      writeFile: (path, value) => {
        written = { path, value };
      },
      now: () => new Date('2026-08-02T13:00:00Z'),
    },
  );
  assert.equal(result.exitCode, 0);
  assert.equal(madeDirectory, '/fixture/results');
  assert.equal(written.path, '/fixture/results/evidence.json');
  const manifest = JSON.parse(written.value);
  assert.equal(manifest.repository, 'cnwenf/Mesh');
  assert.equal(manifest.generatedAt, '2026-08-02T13:00:00.000Z');
  assert.equal(manifest.modelCardSha256, createHash('sha256').update(cardSource).digest('hex'));

  const stdout = [];
  const stderr = [];
  const exitCodes = [];
  const sinks = {
    execute: () => ({ exitCode: 0, stdout: 'written\n', stderr: 'warning\n' }),
    writeStdout: (value) => stdout.push(value),
    writeStderr: (value) => stderr.push(value),
    setExitCode: (value) => exitCodes.push(value),
  };
  assert.equal(runEvidenceProcess([], {}, sinks).exitCode, 0);
  assert.deepEqual(stdout, ['written\n']);
  assert.deepEqual(stderr, ['warning\n']);
  assert.deepEqual(exitCodes, [0]);

  const failed = runEvidenceProcess(
    [],
    {},
    {
      ...sinks,
      execute: () => {
        throw 'failed';
      },
    },
  );
  assert.equal(failed.exitCode, 1);
  assert.match(failed.stderr, /failed/u);
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

    runEvidenceProcess(['--mode', 'plan']);
    assert.equal(process.exitCode, 0);
    runEvidenceProcess(['--unknown']);
    assert.equal(process.exitCode, 1);
  } finally {
    process.stdout.write = originalStdoutWrite;
    process.stderr.write = originalStderrWrite;
    process.exitCode = originalExitCode;
  }
  assert.deepEqual(defaultStdout, ['false\n']);
  assert.equal(defaultStderr.length, 1);
});

test('release summary decomposes unresolved records and expanded visual cells', () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const card = JSON.parse(
    readFileSync(join(frontendRoot, 'model-card/mes108-react-migration.json'), 'utf8'),
  );

  const errors = validateModelCard(card, { frontendRoot, mode: 'release' });
  const summary = errors.find((error) => error.startsWith('release gate: 104 unresolved item(s)'));
  assert.ok(summary);
  for (const fragment of [
    'reconciliation=28',
    'states=6',
    'interactions=30',
    'visualEvidence=28 group(s)/412 cell(s)',
    'components=5',
    'tokens=1',
    'calibrationRisks=6',
  ]) {
    assert.ok(summary.includes(fragment), fragment);
  }
});

test('CI runs an exact-head owner-attested release job without a confirmation skip', () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const workflow = readFileSync(
    resolve(frontendRoot, '../.github/workflows/mes108-model-card.yml'),
    'utf8',
  );
  const guide = readFileSync(resolve(frontendRoot, 'model-card/README.md'), 'utf8');
  const releaseJob = workflow.slice(workflow.indexOf('\n  release:\n'));

  assert.doesNotMatch(workflow, /blueprint\.confirmed/u);
  assert.match(workflow, /^  release:\s*$/mu);
  assert.match(workflow, /issues:\s*read/u);
  assert.match(workflow, /github-pull-request-comment/u);
  assert.match(workflow, /\/mes108-release approve head=/u);
  assert.match(workflow, /author_association == "OWNER"/u);
  assert.match(workflow, /decidedAt:\$decision\.updated_at/u);
  assert.match(workflow, /issues\/\$\{\{ github\.event\.pull_request\.number \}\}\/comments/u);
  assert.doesNotMatch(workflow, /pulls\/.*\/reviews/u);
  assert.match(workflow, /github\.event\.pull_request\.head\.sha/u);
  assert.match(releaseJob, /ref: \$\{\{ github\.event\.pull_request\.head\.sha \}\}/u);
  assert.match(workflow, /--approval-file/u);
  assert.match(workflow, /run-model-card-evidence\.mjs --mode plan/u);
  assert.match(workflow, /run-model-card-evidence\.mjs/u);
  assert.match(workflow, /--evidence-run-file/u);
  assert.match(workflow, /playwright install --with-deps chromium/u);
  assert.match(workflow, /steps\.evidence-plan\.outputs\.required == 'true'/u);
  assert.match(workflow, /--mode release/u);
  const evidenceIndex = releaseJob.indexOf('- name: Produce current-head Playwright evidence');
  const approvalIndex = releaseJob.indexOf(
    '- name: Resolve current-head repository-owner decision',
  );
  const enforcementIndex = releaseJob.indexOf(
    '- name: Enforce evidence and product-owner release gate',
  );
  assert.ok(evidenceIndex >= 0 && evidenceIndex < approvalIndex);
  assert.ok(approvalIndex < enforcementIndex);
  assert.match(releaseJob, /mktemp "\$RUNNER_TEMP\/mes108-release-approval\.XXXXXX\.json"/u);
  assert.match(releaseJob, /steps\.owner-approval\.outputs\.path/u);
  assert.doesNotMatch(releaseJob, /continue-on-error:/u);
  assert.doesNotMatch(releaseJob, /if node -e/u);
  assert.match(guide, /不承担 clean-room 来源与品牌红线扫描/u);
  assert.match(guide, /\.github\/workflows\/source-provenance\.yml/u);
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

  let errors = validateModelCard(card, { frontendRoot: root });
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

  const invalidSupersession = makeCard();
  invalidSupersession.baseline.adoption = 'unknown';
  invalidSupersession.baseline.disposition = 'superseded';
  invalidSupersession.baseline.supersededBy = {
    issue: '',
    pullRequest: 0,
    revision: 'short',
  };
  invalidSupersession.releasePolicy = null;
  errors = validateModelCard(invalidSupersession, { frontendRoot: root });
  for (const fragment of [
    'invalid baseline adoption',
    'baseline.supersededBy.issue must be a non-empty string',
    'baseline.supersededBy.pullRequest must be a positive integer',
    'baseline.supersededBy.revision must be a full lowercase commit SHA',
    'releasePolicy must be an object',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }

  const straySupersession = makeCard();
  straySupersession.baseline.supersededBy = {
    issue: 'MES-999',
    pullRequest: 999,
    revision: 'f'.repeat(40),
  };
  errors = validateModelCard(straySupersession, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('supersededBy must be null')));
});

test('release mode fails while a known calibration risk remains unresolved', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  let errors = validateModelCard(card, { frontendRoot: root, mode: 'release' });
  assert.ok(errors.some((error) => error.includes('calibrationRisks=1')));

  card.calibrationRisks[0].status = 'calibrated';
  errors = validateModelCard(card, { frontendRoot: root, mode: 'release' });
  assert.ok(errors.some((error) => error.includes('calibrationRisks=0')));
});

test('verified interaction evidence must bind e2e test titles to every input mode', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `test('model card interaction', () => {
      const fakeActions = 'page.click(); keyboard.press(); touchscreen.tap();';
      const fakePattern = /page\\.click\\(|keyboard\\.press\\(|touchscreen\\.tap\\(/;
      // page.click(); keyboard.press(); touchscreen.tap();
      void fakeActions;
      void fakePattern;
    });`,
  );
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
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: '',
        inputModes: [],
      },
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'model card interaction',
        config: 'playwright.mes108.config.ts',
        project: 'phone',
        inputModes: ['mouse', 'mouse', 'keyboard'],
      },
    ],
  };

  const errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'evidence must be an object',
    'evidence path must be an e2e spec',
    'evidence testTitle not found',
    'evidence testTitle must be a non-empty string',
    'evidence inputModes must be a non-empty array',
    'evidence has duplicate input mode mouse',
    'evidence has undeclared input mode gamepad',
    'missing file',
    'test does not exercise input mode mouse',
    'test does not exercise input mode keyboard',
    'no evidence covers input mode keyboard',
  ]) {
    assert.ok(
      errors.some((error) => error.includes(fragment)),
      fragment,
    );
  }
});

test('accepts interaction evidence only when its Playwright case exercises every input mode', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  card.pages[0].interactions[0] = {
    id: 'open',
    inputModes: ['mouse', 'keyboard', 'touch'],
    status: 'verified',
    evidence: [
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'model card interaction',
        config: 'playwright.mes108.config.ts',
        project: 'phone',
        inputModes: ['mouse', 'keyboard', 'touch'],
      },
    ],
  };

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.deepEqual(
    errors.filter((error) => error.startsWith('page auth-login interaction open')),
    [],
  );
});

test('matches escaped test titles exactly and ignores actions in comments and strings', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `test(dynamicTitle, () => {});
    test('different title', () => {});
    test('model\\ncard\\tinteraction', async ({ page }) => {
      /* page.screenshot(); page.click(); keyboard.press(); touchscreen.tap(); */
      const fake = "page.screenshot(); page.click(); keyboard.press(); touchscreen.tap();\\\"";
      const templateFake = \`page.screenshot(); page.click(); keyboard.press(); touchscreen.tap();\`;
      await page.getByRole('button').click();
      await page.keyboard.press('Enter');
      await page.touchscreen.tap(1, 1);
      void fake;
      void templateFake;
    });`,
  );
  card.pages[0].interactions[0] = {
    id: 'open',
    inputModes: ['mouse', 'keyboard', 'touch'],
    status: 'verified',
    evidence: [
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'model\ncard\tinteraction',
        config: 'playwright.mes108.config.ts',
        project: 'phone',
        inputModes: ['mouse', 'keyboard', 'touch'],
      },
    ],
  };

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.deepEqual(
    errors.filter((error) => error.startsWith('page auth-login interaction open')),
    [],
  );
});

test('rejects skipped and fixme Playwright cases as release evidence', () => {
  const card = makeCard();
  const root = makeRepoFixture(card);
  writeFixture(
    root,
    'e2e/model-card.spec.ts',
    `test.skip('skipped interaction', async ({ page }) => {
      await page.getByRole('button').click();
      await page.keyboard.press('Enter');
    });
    test.fixme('skipped visual', async ({ page }) => {
      await expect(page).toHaveScreenshot('skipped.png');
      await page.screenshot({ path: 'e2e/evidence/mes108/skipped.png' });
    });`,
  );
  card.pages[0].interactions[0] = {
    id: 'open',
    inputModes: ['mouse', 'keyboard'],
    status: 'verified',
    evidence: [
      {
        path: 'e2e/model-card.spec.ts',
        testTitle: 'skipped interaction',
        config: 'playwright.mes108.config.ts',
        project: 'phone',
        inputModes: ['mouse', 'keyboard'],
      },
    ],
  };
  const artifact = structuredClone(VISUAL_ARTIFACTS[0]);
  artifact.capture.testTitle = 'skipped visual';
  card.pages[0].visualEvidence = [
    {
      viewports: [artifact.viewport],
      themes: [artifact.theme],
      states: [artifact.state],
      status: 'verified',
      artifacts: [artifact],
    },
    {
      viewports: ['390x844', '1440x900'],
      themes: ['light', 'dark'],
      states: [...REQUIRED_PAGE_STATES],
      status: 'pending',
    },
  ];

  const errors = validateModelCard(card, { frontendRoot: root });
  assert.ok(errors.some((error) => error.includes('evidence testTitle not found')));
  assert.ok(errors.some((error) => error.includes('capture.testTitle was not found')));
});

test('pins the repository model card to the accepted MES-142 blueprint revision', () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const card = JSON.parse(
    readFileSync(join(frontendRoot, 'model-card/mes108-react-migration.json'), 'utf8'),
  );

  assert.deepEqual(card.baseline, {
    issue: 'MES-142',
    pullRequest: 100,
    revision: 'b4d579f436121a92cd2684ccd9e86af41004d71d',
    disposition: 'active',
    adoption: 'authoritative',
    supersededBy: null,
  });

  const fontUiToken = card.tokens.find((token) => token.blueprint === '--font-ui');
  assert.equal(fontUiToken?.reconciliation, 'pending');
  assert.equal(
    card.tokens
      .filter((token) => token.blueprint !== '--font-ui')
      .every((token) => token.reconciliation === 'calibrated'),
    true,
    'only the runtime-loaded Inter font remains pending in the token foundation',
  );
  assert.equal(
    card.calibrationRisks.find((risk) => risk.id === 'state-color-contrast')?.status,
    'pending',
  );
  assert.equal(card.calibrationRisks.find((risk) => risk.id === 'font-loading')?.status, 'pending');
  assert.equal(
    card.calibrationRisks.find((risk) => risk.id === 'input-border-contrast')?.status,
    'pending',
  );
  assert.equal(
    card.calibrationRisks.find((risk) => risk.id === 'faint-text-contrast')?.status,
    'pending',
  );
  const tokenDestination = Object.fromEntries(
    card.tokens.map((token) => [token.blueprint, token.react]),
  );
  assert.deepEqual(tokenDestination['--primary'], ['--color-primary']);
  assert.deepEqual(tokenDestination['--input-line'], ['--color-input-border-base']);
  assert.deepEqual(tokenDestination['--ink-faint'], ['--color-text-faint-base']);
  assert.deepEqual(tokenDestination['--disabled-ink'], ['--color-control-disabled-text']);
  assert.deepEqual(tokenDestination['--warning'], ['--color-warning-base']);
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
    null,
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
    'invalid interaction entry',
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
  card.schemaVersion = 1;
  card.issue = 'MES-other';
  card.baseline = null;
  card.dimensions.themes = null;
  card.dimensions.viewports = [];
  card.visualEnvironment = null;
  card.calibrationRisks = null;
  let errors = validateModelCard(card, { frontendRoot: root });
  for (const fragment of [
    'schemaVersion must equal 2',
    'issue must equal MES-108',
    'baseline must be an object',
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

  const invalidBaseline = makeCard();
  invalidBaseline.baseline = {
    issue: 'wrong',
    pullRequest: 101,
    revision: 'short',
    disposition: 'superseded',
    adoption: 'discarded',
    supersededBy: null,
  };
  invalidBaseline.calibrationRisks = [null, { id: '', status: 'pending' }];
  invalidBaseline.calibrationRisks.push({
    id: 'blocked-risk',
    blueprint: 'old',
    react: 'new',
    status: 'blocked',
  });
  errors = validateModelCard(invalidBaseline, { frontendRoot: root });
  for (const fragment of [
    'baseline.issue must equal MES-142',
    'baseline.pullRequest must equal 100',
    'baseline.revision must be a full lowercase commit SHA',
    'superseded baseline requires supersededBy',
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
  assert.match(markdown, /任何输入生命周期与采用方式均不等于 release 批准/);
  assert.doesNotMatch(markdown, /静态输入被取消或部分采用不等于 release 批准/);
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
  assert.deepEqual(
    parseArguments([
      '--mode',
      'release',
      '--approval-file',
      '/tmp/approval.json',
      '--evidence-run-file',
      '/tmp/evidence.json',
      '--release-head',
      'a'.repeat(40),
      '--release-repository',
      'cnwenf/Mesh',
      '--release-owner',
      'cnwenf',
    ]),
    {
      mode: 'release',
      write: false,
      approvalFile: '/tmp/approval.json',
      evidenceRunFile: '/tmp/evidence.json',
      releaseHead: 'a'.repeat(40),
      releaseRepository: 'cnwenf/Mesh',
      releaseOwner: 'cnwenf',
    },
  );
  assert.throws(() => parseArguments(['--unknown']), /unknown argument/u);
  assert.throws(() => parseArguments(['--mode', 'invalid']), /must be audit or release/u);
  assert.throws(() => parseArguments(['--approval-file']), /requires a value/u);
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
  const manifestSource = JSON.stringify({ baseline: { revision: 'a'.repeat(40) } });
  writeFileSync(manifestPath, manifestSource, 'utf8');
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

  const approvalPath = join(root, 'approval.json');
  const evidencePath = join(root, 'evidence.json');
  const approval = { source: 'github-pull-request-comment', reviewer: 'cnwenf' };
  const evidenceRun = { source: 'github-actions-playwright', results: [] };
  writeFileSync(approvalPath, JSON.stringify(approval), 'utf8');
  writeFileSync(evidencePath, JSON.stringify(evidenceRun), 'utf8');
  let observedOptions;
  result = verifyModelCard(
    [
      '--mode',
      'release',
      '--approval-file',
      approvalPath,
      '--evidence-run-file',
      evidencePath,
      '--release-head',
      'c'.repeat(40),
      '--release-repository',
      'cnwenf/Mesh',
      '--release-owner',
      'cnwenf',
    ],
    {
      ...baseOptions,
      validate: (_card, validationOptions) => {
        observedOptions = validationOptions;
        return [];
      },
    },
  );
  assert.equal(result.exitCode, 0);
  assert.deepEqual(observedOptions.releaseApproval, approval);
  assert.deepEqual(observedOptions.evidenceRun, evidenceRun);
  assert.deepEqual(observedOptions.releaseContext, {
    repository: 'cnwenf/Mesh',
    repositoryOwner: 'cnwenf',
    headSha: 'c'.repeat(40),
    modelCardSha256: createHash('sha256').update(manifestSource).digest('hex'),
  });

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

  const cardSource = JSON.stringify({ baseline: { revision: 'b'.repeat(40) } });
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
