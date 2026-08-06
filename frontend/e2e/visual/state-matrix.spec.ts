/**
 * MES-128 exceptional-state visual gate.
 *
 * `theme-visual.spec.ts` remains the owner of the 13 pages x 4 viewports x 2
 * themes normal-state baselines. This suite adds a deterministic 390px phone
 * gate for every applicable exceptional state in design-quality §13.5. The
 * manifest is intentionally executable: a page/state cell cannot disappear
 * without failing the first test, and every N/A cell is tied to production
 * source evidence so it cannot become a permanent silent skip.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { BrowserContext, Locator, Page, Request } from '@playwright/test';
import { applyFonts, commonMasks, PAGES, prepareVisualPage, warmUpPages } from './visual-helpers';

const PAGE_NAMES = [
  '登录',
  '工作台',
  'issue 列表',
  '看板',
  'issue 详情',
  '成员',
  '聊天',
  '运行详情',
  '收件箱',
  '自动值守',
  '集成',
  '洞察',
  '设置',
] as const;

const REQUIRED_STATES = [
  'normal',
  'loading',
  'empty',
  'error',
  'long',
  'offline',
  'permission',
] as const;

const THEMES = ['light', 'dark'] as const;
const API_GLOB = '**/api/v1/**';
const LONG_TEXT =
  '这是一个用于验证极长内容不会制造横向滚动或遮挡关键操作的确定性标题——' +
  'MES-128-long-content-with-an-unbroken-segment-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const LONG_TIMEZONE = 'America/Argentina/ComodRivadavia';
const ISSUE_UUID = '0d3a1f7c-9b2e-4c5a-8f1d-6e7b8c9a0d1e';

const LONG_ISSUE = {
  id: ISSUE_UUID,
  workspace_id: 'ws-1',
  project_id: null,
  project: null,
  identifier_namespace_key: 'MESH',
  number: 1,
  identifier: 'MESH-1',
  title: LONG_TEXT,
  description: LONG_TEXT,
  status: {
    id: 'st-todo',
    project_id: null,
    name: 'Todo',
    category: 'todo',
    color: null,
    position: 0,
    is_default: true,
    allowed_transitions: [],
    created_at: '2026-07-25T08:00:00.000Z',
    updated_at: '2026-07-25T08:00:00.000Z',
  },
  status_id: 'st-todo',
  state_category: 'todo',
  priority: 'high',
  assignee: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
  assignee_id: 'member-human-1',
  reporter: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
  reporter_id: 'member-human-1',
  estimate: null,
  estimate_unit: null,
  due_date: null,
  start_date: null,
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 1,
  created_at: '2026-07-25T08:00:00.000Z',
  updated_at: '2026-07-25T08:00:00.000Z',
};

type PageName = (typeof PAGE_NAMES)[number];
type VisualState = (typeof REQUIRED_STATES)[number];
type JsonRecord = Record<string, unknown>;
type JsonTransform = (payload: unknown) => unknown;

interface ExistingCell {
  readonly kind: 'existing-baseline';
  readonly owner: 'theme-visual.spec.ts';
  readonly reason: string;
}

interface ScenarioCell {
  readonly kind: 'scenario';
}

interface NotApplicableCell {
  readonly kind: 'not-applicable';
  readonly reason: string;
  readonly source: string;
  readonly evidence: RegExp;
}

type StateCell = ExistingCell | ScenarioCell | NotApplicableCell;
type PageStateCells = Record<VisualState, StateCell>;

const existingNormal: ExistingCell = {
  kind: 'existing-baseline',
  owner: 'theme-visual.spec.ts',
  reason: 'the four-viewport, two-theme normal baseline is already gated there',
};
const scenario: ScenarioCell = { kind: 'scenario' };

function fullStatePage(overrides: Partial<Record<VisualState, StateCell>> = {}): PageStateCells {
  return {
    normal: existingNormal,
    loading: scenario,
    empty: scenario,
    error: scenario,
    long: scenario,
    offline: scenario,
    permission: scenario,
    ...overrides,
  };
}

/** 13 pages x 7 states = 91 explicit cells; five cells are code-backed N/A. */
const STATE_MATRIX: Record<PageName, PageStateCells> = {
  登录: fullStatePage({
    empty: {
      kind: 'not-applicable',
      reason: 'login is a task form/result flow, not a data collection with an empty branch',
      source: 'src/shell/pages/LoginPage.tsx',
      evidence: /<form className="mesh-public-flow__form"/,
    },
    permission: {
      kind: 'not-applicable',
      reason: 'login is the public pre-authentication route and therefore has no RBAC denial state',
      source: 'src/App.tsx',
      evidence: /path="\/login"/,
    },
  }),
  工作台: fullStatePage(),
  'issue 列表': fullStatePage(),
  看板: fullStatePage(),
  'issue 详情': fullStatePage(),
  成员: fullStatePage(),
  聊天: fullStatePage(),
  运行详情: fullStatePage(),
  收件箱: fullStatePage(),
  自动值守: fullStatePage(),
  集成: fullStatePage(),
  洞察: fullStatePage(),
  设置: fullStatePage({
    loading: {
      kind: 'not-applicable',
      reason: 'appearance preferences update optimistically and expose no visible loading branch',
      source: 'src/shell/pages/settings/AppearanceSettingsSection.tsx',
      evidence: /setTimezone\(event\.target\.value\);/,
    },
    empty: {
      kind: 'not-applicable',
      reason: 'appearance settings is a fixed preference form, not a data collection',
      source: 'src/shell/pages/settings/AppearanceSettingsSection.tsx',
      evidence: /data-testid="theme-select"/,
    },
    permission: {
      kind: 'not-applicable',
      reason: 'account appearance settings is available to every authenticated user',
      source: 'src/App.tsx',
      evidence: /path="settings" element=\{<SettingsPage/,
    },
  }),
};

interface JsonRule {
  readonly path: string;
  readonly transform: JsonTransform;
  readonly method?: string;
}

interface DataPageFixture {
  readonly primaryPath: string;
  readonly loading: (page: Page) => Locator;
  readonly emptyRules: readonly JsonRule[];
  readonly empty: (page: Page) => Promise<void>;
  readonly longRules: readonly JsonRule[];
  readonly long: (page: Page) => Promise<void>;
}

function record(value: unknown): JsonRecord {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('visual fixture expected a JSON object');
  }
  return value as JsonRecord;
}

function mapData(transform: (data: unknown) => unknown): JsonTransform {
  return (payload) => {
    const envelope = record(payload);
    return { ...envelope, data: transform(envelope.data) };
  };
}

const emptyList = mapData(() => []);

function updateFirstList(patch: JsonRecord): JsonTransform {
  return mapData((data) => {
    if (!Array.isArray(data) || data.length === 0) return data;
    return [{ ...record(data[0]), ...patch }, ...data.slice(1)];
  });
}

const emptyBoard: JsonTransform = (payload) => {
  const envelope = record(payload);
  const groups = Array.isArray(envelope.groups) ? envelope.groups : [];
  return {
    ...envelope,
    groups: groups.map((value) => ({ ...record(value), count: 0, data: [] })),
  };
};

const longBoard: JsonTransform = (payload) => {
  const envelope = record(payload);
  const groups = Array.isArray(envelope.groups) ? envelope.groups : [];
  let changed = false;
  return {
    ...envelope,
    groups: groups.map((value) => {
      const group = record(value);
      const data = Array.isArray(group.data) ? group.data : [];
      if (changed || data.length === 0) return group;
      changed = true;
      return {
        ...group,
        data: [{ ...record(data[0]), title: LONG_TEXT }, ...data.slice(1)],
      };
    }),
  };
};

const emptyExecution = mapData((value) => {
  const execution = record(value);
  return { ...execution, result: null, credentials: [] };
});

const emptyLogs = mapData((value) => ({ ...record(value), lines: [], next_offset: 0 }));
const longLogs = mapData((value) => ({
  ...record(value),
  lines: [{ stream: 'stdout', offset: 0, line: LONG_TEXT }],
  next_offset: 1,
}));

const emptyDashboard = mapData((value) => {
  const dashboard = record(value);
  const throughput = record(dashboard.throughput);
  const workload = record(dashboard.workload);
  const agentStats = record(dashboard.agent_stats);
  return {
    ...dashboard,
    throughput: { ...throughput, series: [] },
    workload: { ...workload, data: [] },
    agent_stats: { ...agentStats, agents: [] },
  };
});

const longDashboard = mapData((value) => {
  const dashboard = record(value);
  const workload = record(dashboard.workload);
  const rows = Array.isArray(workload.data) ? workload.data : [];
  const agentStats = record(dashboard.agent_stats);
  const agents = Array.isArray(agentStats.agents) ? agentStats.agents : [];
  return {
    ...dashboard,
    workload: {
      ...workload,
      data:
        rows.length === 0
          ? rows
          : [{ ...record(rows[0]), display_name: LONG_TEXT }, ...rows.slice(1)],
    },
    agent_stats: {
      ...agentStats,
      agents:
        agents.length === 0
          ? agents
          : [{ ...record(agents[0]), display_name: LONG_TEXT }, ...agents.slice(1)],
    },
  };
});

const DATA_FIXTURES: Record<Exclude<PageName, '登录' | '设置'>, DataPageFixture> = {
  工作台: {
    primaryPath: '/api/v1/workspaces/ws-1/issues',
    loading: (page) => page.getByTestId('home-dashboard').locator('.mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/issues', transform: emptyList }],
    empty: async (page) => expect(page.getByTestId('home-onboarding')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/issues',
        transform: mapData(() => [LONG_ISSUE]),
      },
    ],
    long: async (page) => expect(page.getByText(LONG_TEXT, { exact: true })).toBeVisible(),
  },
  'issue 列表': {
    primaryPath: '/api/v1/workspaces/ws-1/issues',
    loading: (page) => page.getByTestId('issues-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/issues', transform: emptyList }],
    empty: async (page) => expect(page.locator('.mesh-empty-state')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/issues',
        transform: mapData(() => [LONG_ISSUE]),
      },
    ],
    long: async (page) => expect(page.getByText(LONG_TEXT, { exact: true })).toBeVisible(),
  },
  看板: {
    primaryPath: '/api/v1/views/view-1/issues',
    loading: (page) => page.getByTestId('board-page').locator('.mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/views/view-1/issues', transform: emptyBoard }],
    empty: async (page) => {
      await expect(page.getByTestId('board-compact')).toBeVisible();
      await expect(page.locator('[data-testid^="board-card-"]')).toHaveCount(0);
    },
    longRules: [{ path: '/api/v1/views/view-1/issues', transform: longBoard }],
    long: async (page) => expect(page.getByText(LONG_TEXT, { exact: true })).toBeVisible(),
  },
  'issue 详情': {
    primaryPath: `/api/v1/issues/${ISSUE_UUID}`,
    loading: (page) => page.locator('.mesh-skeleton'),
    emptyRules: [
      {
        path: `/api/v1/issues/${ISSUE_UUID}`,
        transform: mapData((value) => ({
          ...record(value),
          children_progress: { total: 0, done: 0 },
        })),
      },
      { path: `/api/v1/issues/${ISSUE_UUID}/children`, transform: emptyList },
      { path: `/api/v1/issues/${ISSUE_UUID}/dependencies`, transform: emptyList },
      { path: `/api/v1/issues/${ISSUE_UUID}/activity`, transform: emptyList },
      { path: `/api/v1/issues/${ISSUE_UUID}/comments`, transform: emptyList },
      { path: `/api/v1/issues/${ISSUE_UUID}/attachments`, transform: emptyList },
      // MES-188 批次②:issue 详情执行反查面板(runtime.md §4.5)——零运行
      // 时渲染第三个 .mesh-issues-detail__empty 占位。
      { path: '/api/v1/workspaces/ws-1/executions', transform: emptyList },
    ],
    empty: async (page) => {
      await expect(page.getByTestId('comments-empty')).toBeVisible();
      await expect(page.getByTestId('attachments-empty')).toBeVisible();
      await expect(page.locator('.mesh-issues-detail__empty')).toHaveCount(3);
      await expect(page.getByText(/0\/0/)).toBeVisible();
    },
    longRules: [
      {
        path: `/api/v1/issues/${ISSUE_UUID}`,
        transform: mapData((value) => ({
          ...record(value),
          title: LONG_TEXT,
          description: LONG_TEXT,
        })),
      },
      {
        path: `/api/v1/issues/${ISSUE_UUID}/comments`,
        transform: updateFirstList({ body: LONG_TEXT, content: LONG_TEXT }),
      },
    ],
    long: async (page) => {
      await expect(page.getByTestId('issue-detail-title')).toHaveValue(LONG_TEXT);
      await expect(page.getByTestId('issue-detail-description')).toContainText(LONG_TEXT);
    },
  },
  成员: {
    primaryPath: '/api/v1/workspaces/ws-1/members',
    loading: (page) => page.locator('.mesh-members .mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/members', transform: emptyList }],
    empty: async (page) => expect(page.getByTestId('members-empty-invite')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/members',
        transform: mapData((data) => {
          if (!Array.isArray(data) || data.length === 0) return data;
          const first = record(data[0]);
          const profile = record(first.profile);
          return [
            {
              ...first,
              display_name: LONG_TEXT,
              profile: { ...profile, full_name: LONG_TEXT },
            },
            ...data.slice(1),
          ];
        }),
      },
    ],
    long: async (page) =>
      expect(
        page.getByTestId('member-card-open-member-human-1').getByText(LONG_TEXT, { exact: true }),
      ).toBeVisible(),
  },
  聊天: {
    primaryPath: '/api/v1/workspaces/ws-1/chat-sessions',
    loading: (page) => page.getByTestId('chat-session-panel').locator('.mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/chat-sessions', transform: emptyList }],
    empty: async (page) => expect(page.getByTestId('chat-empty-new')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/chat-sessions',
        transform: updateFirstList({ title: LONG_TEXT, last_message_preview: LONG_TEXT }),
      },
      {
        path: '/api/v1/workspaces/ws-1/chat-sessions/sess-1/messages',
        transform: updateFirstList({ content: LONG_TEXT }),
      },
    ],
    long: async (page) => {
      await page.getByTestId('chat-session-sess-1').click();
      await expect(page.getByTestId('chat-message-msg-2')).toContainText(LONG_TEXT);
    },
  },
  运行详情: {
    primaryPath: '/api/v1/workspaces/ws-1/executions/exec-1',
    loading: (page) => page.locator('.mesh-executions .mesh-skeleton'),
    emptyRules: [
      { path: '/api/v1/workspaces/ws-1/executions/exec-1', transform: emptyExecution },
      { path: '/api/v1/workspaces/ws-1/executions/exec-1/logs', transform: emptyLogs },
    ],
    empty: async (page) => expect(page.locator('.mesh-executions__log-empty')).toBeVisible(),
    longRules: [{ path: '/api/v1/workspaces/ws-1/executions/exec-1/logs', transform: longLogs }],
    long: async (page) => expect(page.getByTestId('execution-log-line-0')).toContainText(LONG_TEXT),
  },
  收件箱: {
    primaryPath: '/api/v1/inbox',
    loading: (page) => page.locator('.mesh-inbox__skeleton'),
    emptyRules: [{ path: '/api/v1/inbox', transform: emptyList }],
    empty: async (page) => expect(page.getByTestId('inbox-empty-action')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/inbox',
        transform: updateFirstList({ title: LONG_TEXT, preview: LONG_TEXT }),
      },
    ],
    long: async (page) => expect(page.getByTestId('inbox-row-n-1')).toContainText(LONG_TEXT),
  },
  自动值守: {
    primaryPath: '/api/v1/workspaces/ws-1/autopilots',
    loading: (page) => page.getByTestId('autopilots-page').locator('.mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/autopilots', transform: emptyList }],
    empty: async (page) => expect(page.getByTestId('autopilot-empty-create')).toBeVisible(),
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/autopilots',
        transform: updateFirstList({ name: LONG_TEXT, description: LONG_TEXT }),
      },
    ],
    long: async (page) =>
      expect(page.getByTestId('autopilot-name-autopilot-1')).toContainText(LONG_TEXT),
  },
  集成: {
    primaryPath: '/api/v1/workspaces/ws-1/integrations',
    loading: (page) => page.getByTestId('integrations-page').locator(':scope > .mesh-skeleton'),
    emptyRules: [{ path: '/api/v1/workspaces/ws-1/integrations', transform: emptyList }],
    empty: async (page) => {
      await expect(
        page.getByTestId('integrations-page').locator('.mesh-empty-state').first(),
      ).toBeVisible();
    },
    longRules: [
      {
        path: '/api/v1/workspaces/ws-1/integrations',
        transform: updateFirstList({ name: LONG_TEXT }),
      },
    ],
    long: async (page) =>
      expect(page.getByTestId('integration-name-integration-1')).toContainText(LONG_TEXT),
  },
  洞察: {
    primaryPath: '/api/v1/workspaces/ws-1/dashboards/workspace',
    loading: (page) => page.getByTestId('insights-loading'),
    emptyRules: [
      { path: '/api/v1/workspaces/ws-1/dashboards/workspace', transform: emptyDashboard },
    ],
    empty: async (page) => expect(page.locator('.mesh-empty-state')).toBeVisible(),
    longRules: [{ path: '/api/v1/workspaces/ws-1/dashboards/workspace', transform: longDashboard }],
    long: async (page) => expect(page.getByText(LONG_TEXT, { exact: true }).first()).toBeVisible(),
  },
};

function matches(request: Request, path: string, method = 'GET'): boolean {
  return request.method() === method && new URL(request.url()).pathname === path;
}

async function installRules(page: Page, rules: readonly JsonRule[]): Promise<void> {
  await page.route(API_GLOB, async (route, request) => {
    const rule = rules.find((candidate) =>
      matches(request, candidate.path, candidate.method ?? 'GET'),
    );
    if (rule === undefined) {
      await route.fallback();
      return;
    }
    const upstream = await route.fetch();
    const payload: unknown = await upstream.json();
    await route.fulfill({ response: upstream, json: rule.transform(payload) });
  });
}

interface HeldRoute {
  readonly seen: Promise<void>;
  readonly release: () => void;
}

async function installHeldRoute(page: Page, path: string, method = 'GET'): Promise<HeldRoute> {
  let markSeen: (() => void) | undefined;
  let release: (() => void) | undefined;
  const seen = new Promise<void>((resolveSeen) => {
    markSeen = resolveSeen;
  });
  const gate = new Promise<void>((resolveGate) => {
    release = resolveGate;
  });
  await page.route(API_GLOB, async (route, request) => {
    if (!matches(request, path, method)) {
      await route.fallback();
      return;
    }
    const upstream = await route.fetch();
    markSeen?.();
    await gate;
    await route.fulfill({ response: upstream });
  });
  return {
    seen,
    release: () => release?.(),
  };
}

async function installFailure(
  page: Page,
  path: string,
  status: 403 | 500,
  method = 'GET',
): Promise<void> {
  await page.route(API_GLOB, async (route, request) => {
    if (!matches(request, path, method)) {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      headers: { 'access-control-allow-origin': '*' },
      json: {
        error: {
          code: status === 403 ? 'forbidden' : 'internal_error',
          message: status === 403 ? 'permission denied' : 'deterministic visual failure',
        },
      },
    });
  });
}

async function gotoPath(page: Page, pageName: PageName): Promise<void> {
  await page.goto(PAGES[pageName].path, { waitUntil: 'domcontentloaded' });
}

async function waitForError(page: Page): Promise<void> {
  await expect(page.locator('.mesh-error-state')).toBeVisible();
}

async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth, JSON.stringify(dimensions)).toBeLessThanOrEqual(
    dimensions.clientWidth + 1,
  );
}

async function triggerOffline(context: BrowserContext, page: Page): Promise<void> {
  // Font assets must be resident before Chromium's context is disconnected.
  // Otherwise a cold CI context resolves document.fonts.ready with fallback
  // glyphs and produces host-dependent offline screenshots.
  await applyFonts(page);
  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await expect(page.getByTestId('status-banner-resyncing')).toBeVisible();
}

function guestMembership(payload: unknown): unknown {
  const envelope = record(payload);
  const data = record(envelope.data);
  const memberships = Array.isArray(data.memberships) ? data.memberships : [];
  return {
    ...envelope,
    data: {
      ...data,
      memberships: memberships.map((value) => ({ ...record(value), role: 'guest' })),
    },
  };
}

function readOnlyViews(payload: unknown): unknown {
  return updateFirstList({ can_write: false })(payload);
}

async function runDataScenario(
  page: Page,
  context: BrowserContext,
  pageName: Exclude<PageName, '登录' | '设置'>,
  state: Exclude<VisualState, 'normal'>,
): Promise<() => Promise<void>> {
  const fixture = DATA_FIXTURES[pageName];
  if (state === 'loading') {
    const held = await installHeldRoute(page, fixture.primaryPath);
    await gotoPath(page, pageName);
    await held.seen;
    await expect(fixture.loading(page)).toBeVisible();
    return async () => held.release();
  }
  if (state === 'empty') {
    await installRules(page, fixture.emptyRules);
    await gotoPath(page, pageName);
    await fixture.empty(page);
    return async () => {};
  }
  if (state === 'error') {
    await installFailure(page, fixture.primaryPath, 500);
    await gotoPath(page, pageName);
    await waitForError(page);
    return async () => {};
  }
  if (state === 'long') {
    await installRules(page, fixture.longRules);
    await gotoPath(page, pageName);
    await fixture.long(page);
    return async () => {};
  }
  if (state === 'offline') {
    await gotoPath(page, pageName);
    await PAGES[pageName].ready(page);
    if (PAGES[pageName].interact !== undefined) await PAGES[pageName].interact(page);
    await triggerOffline(context, page);
    return async () => context.setOffline(false);
  }
  if (pageName === '看板') {
    await installRules(page, [{ path: '/api/v1/workspaces/ws-1/views', transform: readOnlyViews }]);
    await gotoPath(page, pageName);
    await PAGES[pageName].ready(page);
    await expect(page.getByTestId('group-by-select')).toBeDisabled();
    return async () => {};
  }
  if (pageName === '成员' || pageName === '集成') {
    await installRules(page, [{ path: '/api/v1/users/me', transform: guestMembership }]);
    await gotoPath(page, pageName);
    await PAGES[pageName].ready(page);
    if (pageName === '成员') {
      await expect(page.getByTestId('invite-human-button')).toHaveCount(0);
      await expect(page.getByTestId('card-role-select-member-human-1')).toBeDisabled();
    } else {
      await expect(page.getByTestId('integrations-readonly-banner')).toBeVisible();
      await expect(page.getByTestId('integration-create')).toHaveCount(0);
    }
    return async () => {};
  }
  await installFailure(page, fixture.primaryPath, 403);
  await gotoPath(page, pageName);
  if (pageName === '洞察') {
    // Analytics deliberately leaves the workspace shell on a 403 so the
    // denied workspace cannot leak through its navigation or global overlays.
    // Validate that isolated recovery contract instead of waiting for the
    // in-shell ErrorState used by the remaining data pages.
    await expect(page.getByTestId('forbidden-page')).toBeVisible();
    await expect(page).toHaveURL(/\/forbidden\?workspace=%2Fw%2Facme$/);
    await expect(page.getByTestId('forbidden-contact-action')).toHaveAttribute(
      'href',
      '/w/acme/members',
    );
    await expect(page.getByTestId('forbidden-workspace')).toHaveAttribute('href', '/w/acme');
    return async () => {};
  }
  await waitForError(page);
  return async () => {};
}

async function fillLogin(page: Page): Promise<void> {
  await page.getByTestId('login-email').fill('visual-state@example.test');
  await page.getByTestId('login-password').fill('Correct-Horse-Battery-42');
}

async function runLoginScenario(
  page: Page,
  context: BrowserContext,
  state: 'loading' | 'error' | 'long' | 'offline',
): Promise<() => Promise<void>> {
  await gotoPath(page, '登录');
  await PAGES.登录.ready(page);
  if (state === 'long') {
    await page.getByTestId('login-email').fill(`${LONG_TEXT}@example.test`);
    await page.getByTestId('login-password').fill(LONG_TEXT);
    await expect(page.getByTestId('login-email')).toHaveValue(`${LONG_TEXT}@example.test`);
    return async () => {};
  }
  if (state === 'offline') {
    await applyFonts(page);
    await context.setOffline(true);
    await fillLogin(page);
    await page.getByTestId('login-account-submit').click();
    await expect(page.getByTestId('login-error')).toBeVisible();
    return async () => context.setOffline(false);
  }
  if (state === 'error') {
    await installFailure(page, '/api/v1/auth/login', 500, 'POST');
    await fillLogin(page);
    await page.getByTestId('login-account-submit').click();
    await expect(page.getByTestId('login-error')).toBeVisible();
    return async () => {};
  }
  const held = await installHeldRoute(page, '/api/v1/auth/login', 'POST');
  await fillLogin(page);
  await page.getByTestId('login-account-submit').click();
  await held.seen;
  await expect(page.getByTestId('login-account-submit')).toBeDisabled();
  return async () => held.release();
}

async function runSettingsScenario(
  page: Page,
  context: BrowserContext,
  state: 'error' | 'long' | 'offline',
): Promise<() => Promise<void>> {
  await gotoPath(page, '设置');
  await PAGES.设置.ready(page);
  if (state === 'long') {
    await expect(page.getByTestId('timezone-select')).toHaveValue(LONG_TIMEZONE);
    await expect(page.getByTestId('tz-sample')).toBeVisible();
    return async () => {};
  }
  if (state === 'error') {
    await installFailure(page, '/api/v1/users/me', 500, 'PATCH');
  } else {
    await applyFonts(page);
    await context.setOffline(true);
  }
  await page.getByTestId('timezone-select').selectOption('Asia/Shanghai');
  await expect(page.locator('.mesh-banner--danger')).toBeVisible();
  return async () => {
    if (state === 'offline') await context.setOffline(false);
  };
}

async function executeScenario(
  page: Page,
  context: BrowserContext,
  pageName: PageName,
  state: Exclude<VisualState, 'normal'>,
): Promise<() => Promise<void>> {
  if (pageName === '登录') {
    return runLoginScenario(page, context, state as 'loading' | 'error' | 'long' | 'offline');
  }
  if (pageName === '设置') {
    return runSettingsScenario(page, context, state as 'error' | 'long' | 'offline');
  }
  return runDataScenario(page, context, pageName, state);
}

test.describe('MES-128 explicit exceptional-state matrix', () => {
  test.beforeAll(async ({ browser }) => {
    await warmUpPages(browser);
  });

  test('manifest accounts for exactly 91 cells and 146 exceptional snapshots', () => {
    expect(Object.keys(PAGES).sort()).toEqual([...PAGE_NAMES].sort());
    const expectedCells = PAGE_NAMES.flatMap((pageName) =>
      REQUIRED_STATES.map((state) => `${pageName}:${state}`),
    );
    const actualCells = Object.entries(STATE_MATRIX).flatMap(([pageName, states]) =>
      Object.keys(states).map((state) => `${pageName}:${state}`),
    );
    expect(actualCells.sort()).toEqual(expectedCells.sort());

    const cells = Object.values(STATE_MATRIX).flatMap((states) => Object.values(states));
    expect(cells.filter((cell) => cell.kind === 'existing-baseline')).toHaveLength(13);
    expect(cells.filter((cell) => cell.kind === 'not-applicable')).toHaveLength(5);
    expect(cells.filter((cell) => cell.kind === 'scenario')).toHaveLength(73);
    expect(cells.filter((cell) => cell.kind === 'scenario').length * THEMES.length).toBe(146);
  });

  for (const pageName of PAGE_NAMES) {
    for (const state of REQUIRED_STATES) {
      const cell = STATE_MATRIX[pageName][state];
      if (cell.kind !== 'not-applicable') continue;
      test(`${pageName} ${state} has current production-code evidence for N/A`, () => {
        expect(cell.reason.length).toBeGreaterThan(20);
        const source = readFileSync(resolve(process.cwd(), cell.source), 'utf8');
        expect(source).toMatch(cell.evidence);
      });
    }
  }

  for (const pageName of PAGE_NAMES) {
    for (const state of REQUIRED_STATES) {
      const cell = STATE_MATRIX[pageName][state];
      if (cell.kind !== 'scenario') continue;
      for (const theme of THEMES) {
        test(`${pageName} ${state} ${theme}`, async ({ page, context }) => {
          await prepareVisualPage(
            page,
            theme,
            pageName === '设置' && state === 'long'
              ? { locale: 'en', timezone: LONG_TIMEZONE }
              : {},
          );
          let cleanup: () => Promise<void> = async () => {};
          try {
            cleanup = await executeScenario(
              page,
              context,
              pageName,
              state as Exclude<VisualState, 'normal'>,
            );
            if (state !== 'offline') await applyFonts(page);
            await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
            await assertNoHorizontalOverflow(page);

            const pageSpec = PAGES[pageName];
            const masks =
              state === 'offline'
                ? [page.locator('[data-testid="conn-status"]'), ...pageSpec.masks(page)]
                : [...commonMasks(page), ...pageSpec.masks(page)];
            await expect(page).toHaveScreenshot(`${pageSpec.snapshotKey}-${state}-${theme}.png`, {
              fullPage: true,
              mask: masks,
              maxDiffPixelRatio: 0.01,
            });
          } finally {
            await cleanup();
          }
        });
      }
    }
  }
});
