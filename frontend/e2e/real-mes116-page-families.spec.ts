/**
 * MES-116 real-browser acceptance for the remaining platform/admin families.
 * Data is created through the real API, then every target surface is rendered
 * and operated in Chromium. The 320px pass asserts that wide tables remain in
 * their own overflow boundary rather than widening the document.
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { injectSession } from './helpers';

const API_BASE = process.env.MES116_API_BASE ?? 'http://127.0.0.1:8000';
const EVIDENCE_DIR = resolve('e2e', 'evidence', 'mes116');
const PASSWORD = 'MES-116-Real#2026';
const RUN = `${Date.now().toString(36)}${Math.floor(Math.random() * 10_000).toString(36)}`;

interface World {
  token: string;
  workspaceId: string;
  workspaceSlug: string;
  projectId: string;
  cycleId: string;
  agentId: string;
  skillId: string;
  squadId: string;
  taskId: string;
  autopilotId: string;
  runtimeId: string;
  integrationId: string;
  subscriptionId: string;
}

interface ReadyRoute {
  key: string;
  path: string;
  testId: string;
}

let token = '';
let world: World;

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token === '' ? {} : { Authorization: `Bearer ${token}` }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const payload = text === '' ? {} : (JSON.parse(text) as Record<string, unknown>);
  if (!response.ok) {
    throw new Error(`${method} ${path} -> ${response.status}: ${text}`);
  }
  return (payload.data ?? payload) as T;
}

async function bootstrapWorld(): Promise<World> {
  const email = `mes116-${RUN}@example.com`;
  const register = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD, display_name: 'MES-116 Owner' }),
  });
  if (register.status !== 201) {
    throw new Error(`register -> ${register.status}: ${await register.text()}`);
  }
  const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  if (!login.ok) throw new Error(`login -> ${login.status}: ${await login.text()}`);
  token = ((await login.json()) as { data: { access_token: string } }).data.access_token;

  const workspaceSlug = `mes116-${RUN}`.slice(0, 48);
  const workspace = await request<{ id: string }>('POST', '/api/v1/workspaces', {
    name: 'MES-116 responsive acceptance workspace',
    slug: workspaceSlug,
  });
  const workspaceId = workspace.id;
  const members = await request<Array<{ id: string; member_type: string }>>(
    'GET',
    `/api/v1/workspaces/${workspaceId}/members`,
  );
  const ownerId = members.find((member) => member.member_type === 'human')?.id;
  if (ownerId === undefined) throw new Error('workspace owner roster row missing');

  const project = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/projects`,
    {
      name: 'Long platform migration project with resilient responsive administration',
      key: `M${String(Date.now()).slice(-7)}`,
      description:
        `MES-116 long-content pressure ${'跨设备可读内容'.repeat(24)} ` +
        `https://example.com/${'unbroken-segment-'.repeat(18)}`,
      status: 'active',
      visibility: 'public',
      start_date: '2026-08-01',
      target_date: '2026-12-31',
    },
  );
  const cycle = await request<{ id: string }>('POST', `/api/v1/workspaces/${workspaceId}/cycles`, {
    name: 'August reliability and responsive quality cycle',
    starts_at: '2026-08-01',
    ends_at: '2026-08-31',
    project_id: project.id,
    state: 'active',
    auto_roll: true,
  });
  const agent = await request<{ id: string }>('POST', `/api/v1/workspaces/${workspaceId}/agents`, {
    name: 'Responsive platform reliability engineering agent',
    role_tag: 'Platform reliability and design quality',
    bio: `${'Long agent biography for overflow pressure. '.repeat(12)}END`,
    visibility: 'workspace',
    system_instructions: 'Keep changes auditable, test-first, and accessible.',
  });

  const capabilityGrants = [
    { capability: 'read:code', permission: 'read_only' },
    { capability: 'write:comment', permission: 'write' },
    { capability: 'exec:shell', permission: 'confirm_required' },
  ];
  const skill = await request<{ id: string }>('POST', `/api/v1/workspaces/${workspaceId}/skills`, {
    name: 'Production readiness and accessible interface verification',
    slug: `mes116-readiness-${RUN}`.slice(0, 90),
    summary: `${'Verify real behavior, permissions, and long-content layout. '.repeat(10)}END`,
    tags: ['quality', 'accessibility', 'operations'],
    required_capabilities: capabilityGrants,
  });
  const version = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/skills/${skill.id}/versions`,
    {
      version: '1.0.0',
      instructions: '# Verification\nRun scoped tests, real browser flows, and overflow checks.',
      scripts: [
        {
          path: 'scripts/verify.sh',
          runtime: 'shell',
          entrypoint: true,
          required_capabilities: [capabilityGrants[2]],
          content: '#!/bin/sh\necho verified',
        },
      ],
      references: [{ path: 'docs/checklist.md', content: '# Responsive checklist' }],
      triggers: [{ trigger_type: 'keyword', pattern: 'verify', weight: 2 }],
      required_capabilities: capabilityGrants,
      publish: true,
    },
  );
  const installation = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/skill-installations`,
    { skill_id: skill.id, skill_version_id: version.id, scope: 'workspace' },
  );
  await request('POST', `/api/v1/workspaces/${workspaceId}/agents/${agent.id}/skills`, {
    skill_installation_id: installation.id,
    priority: 120,
  });

  const squad = await request<{ id: string }>('POST', `/api/v1/workspaces/${workspaceId}/squads`, {
    name: 'Platform quality response squad with a deliberately descriptive name',
    description: `${'Cross-functional response ownership and audit context. '.repeat(8)}END`,
    instructions: 'Plan, verify dependencies, and report durable outcomes.',
    require_plan_approval: true,
    members: [{ member_id: ownerId, role: 'leader' }],
  });
  const issue = await request<{ id: string }>('POST', `/api/v1/workspaces/${workspaceId}/issues`, {
    title: 'Verify every remaining page family across long content and narrow viewports',
    description: `${'Real squad task pressure data. '.repeat(20)}END`,
    project_id: project.id,
    cycle_id: cycle.id,
  });
  const task = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/squads/${squad.id}/tasks`,
    { issue_id: issue.id, brief: 'Execute a real responsive verification plan.' },
  );

  const autopilot = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/autopilots`,
    {
      name: 'Weekday platform quality summary with long but readable operational context',
      description: `${'Scheduled verification and notification workflow. '.repeat(8)}END`,
      trigger_type: 'schedule',
      trigger_config: { cron: '0 9 * * 1-5', timezone: 'Asia/Shanghai' },
      action_config: [{ type: 'send_notification', message: 'Quality summary is ready.' }],
      status: 'active',
      rate_limit_max: 10,
      rate_limit_window_seconds: 3600,
    },
  );
  const runtime = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/runtimes`,
    {
      name: 'intranet-responsive-verification-runtime-with-long-name',
      kind: 'self_hosted',
      labels: { region: 'intranet', purpose: 'mes116-responsive-verification' },
      max_concurrent: 4,
    },
  );
  const integrationResult = await request<{ id?: string; integration?: { id: string } }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/integrations`,
    {
      kind: 'webhook_outbound',
      name: 'External delivery integration with long operational ownership context',
      config: {},
    },
  );
  const integrationId = integrationResult.integration?.id ?? integrationResult.id;
  if (integrationId === undefined) throw new Error('integration id missing');
  const subscription = await request<{ id: string }>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/webhook-subscriptions`,
    {
      url: `https://example.com/hooks/${'long-segment-'.repeat(14)}${RUN}`,
      event_types: ['issue.created'],
      integration_id: integrationId,
    },
  );

  return {
    token,
    workspaceId,
    workspaceSlug,
    projectId: project.id,
    cycleId: cycle.id,
    agentId: agent.id,
    skillId: skill.id,
    squadId: squad.id,
    taskId: task.id,
    autopilotId: autopilot.id,
    runtimeId: runtime.id,
    integrationId,
    subscriptionId: subscription.id,
  };
}

function targetRoutes(): ReadyRoute[] {
  return [
    { key: '01-projects', path: '/projects', testId: 'projects-view' },
    {
      key: '02-project-detail',
      path: `/projects/${world.projectId}`,
      testId: 'project-detail-header',
    },
    {
      key: '03-project-settings',
      path: `/projects/${world.projectId}/settings`,
      testId: 'settings-form',
    },
    { key: '04-cycles', path: '/cycles', testId: `cycle-row-${world.cycleId}` },
    {
      key: '05-agent-detail',
      path: `/agents/${world.agentId}`,
      testId: 'agent-detail-page',
    },
    { key: '06-squads', path: '/squads', testId: 'squad-grid' },
    {
      key: '07-squad-detail',
      path: `/squads/${world.squadId}`,
      testId: 'squad-detail-page',
    },
    {
      key: '08-squad-task',
      path: `/squads/${world.squadId}/tasks/${world.taskId}`,
      testId: 'squad-task-page',
    },
    { key: '09-autopilots', path: '/autopilots', testId: 'autopilots-page' },
    { key: '10-autopilot-new', path: '/autopilots/new', testId: 'autopilot-editor' },
    {
      key: '11-autopilot-detail',
      path: `/autopilots/${world.autopilotId}`,
      testId: 'autopilot-detail',
    },
    { key: '12-webhook-config', path: '/webhooks', testId: 'webhook-config-page' },
    { key: '13-runtimes', path: '/runtimes', testId: 'runtimes-table' },
    {
      key: '14-runtime-detail',
      path: `/runtimes/${world.runtimeId}`,
      testId: 'runtime-detail-page',
    },
    { key: '15-skills', path: '/skills', testId: 'skills-page-title' },
    {
      key: '16-skill-marketplace',
      path: '/skills/marketplace',
      testId: 'marketplace-title',
    },
    {
      key: '17-skill-detail',
      path: `/skills/${world.skillId}`,
      testId: 'skill-detail',
    },
    { key: '18-integrations', path: '/integrations', testId: 'integrations-page' },
    {
      key: '19-integration-detail',
      path: `/integrations/${world.integrationId}`,
      testId: 'integration-detail',
    },
    {
      key: '20-webhook-subscriptions',
      path: '/webhook-subscriptions',
      testId: `webhook-card-${world.subscriptionId}`,
    },
    {
      key: '21-labels',
      path: `/w/${world.workspaceSlug}/settings/labels`,
      testId: 'labels-panel',
    },
    {
      key: '22-custom-fields',
      path: `/w/${world.workspaceSlug}/settings/custom-fields`,
      testId: 'custom-fields-panel',
    },
    {
      key: '23-data-management',
      path: `/w/${world.workspaceSlug}/settings/data`,
      testId: 'data-management-section',
    },
    {
      key: '24-not-found',
      path: '/this-route-does-not-exist',
      testId: 'notfound-home',
    },
    {
      key: '25-oauth-error',
      path: '/auth/oauth/callback/github',
      testId: 'oauth-callback-error',
    },
  ];
}

async function dismissOnboarding(page: Page): Promise<void> {
  const dismiss = page.getByText("Don't show again");
  if (await dismiss.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await dismiss.click();
    await expect(dismiss).not.toBeVisible();
  }
}

async function openReady(page: Page, route: ReadyRoute): Promise<void> {
  await page.goto(route.path);
  const target = page.getByTestId(route.testId).first();
  // A freshly bootstrapped real workspace can finish resolving its active
  // workspace context just after the first client-side route mount. Recover
  // once with a new document navigation; response/page-error collectors still
  // fail the scenario if the first attempt exposed a real server/runtime error.
  if (!(await target.isVisible({ timeout: 10_000 }).catch(() => false))) {
    await page.goto(route.path);
  }
  await expect(target).toBeVisible();
  // The checklist is loaded asynchronously after the target page. Dismiss it
  // after readiness so screenshots cannot accidentally capture the overlay.
  await dismissOnboarding(page);
}

async function prepareEvidenceState(page: Page, route: ReadyRoute): Promise<void> {
  if (route.key === '05-agent-detail') {
    await page.getByTestId('agent-tab-skills').click();
    await expect(page.getByTestId('agent-tools-table')).toBeVisible();
    await expect(page.getByTestId('agent-tool-enabled-exec:shell')).not.toBeChecked();
    await expect(page.getByTestId('agent-tool-permission-exec:shell')).toHaveValue('read_only');
  }
  if (route.key === '08-squad-task') {
    const kanban = page.getByTestId('squad-view-kanban');
    await kanban.click();
    await expect(kanban).toHaveAttribute('aria-selected', 'true');
  }
  if (route.key === '19-integration-detail') {
    await page.getByTestId('integration-tab-health').click();
    await expect(page.getByTestId('integration-health-panel')).toBeVisible();
  }
  if (route.key === '20-webhook-subscriptions') {
    await page.getByTestId(`webhook-expand-${world.subscriptionId}`).click();
    await expect(page.getByTestId(`webhook-detail-${world.subscriptionId}`)).toBeVisible();
  }
}

async function setTheme(page: Page, mode: 'light' | 'dark'): Promise<void> {
  await page.goto('/settings/appearance');
  await dismissOnboarding(page);
  const select = page.getByTestId('theme-select');
  await expect(select).toBeVisible();
  if ((await select.inputValue()) !== mode) await select.selectOption(mode);
  await expect(page.locator('html')).toHaveAttribute('data-theme', mode);
}

async function expectNoDocumentOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(
    dimensions.scrollWidth,
    `document overflowed by ${dimensions.scrollWidth - dimensions.clientWidth}px at ${page.url()}`,
  ).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

function collectUnexpectedFailures(page: Page): {
  pageErrors: string[];
  serverErrors: string[];
} {
  const pageErrors: string[] = [];
  const serverErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(`${error.message} at ${page.url()}`));
  page.on('response', (response) => {
    if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`);
  });
  return { pageErrors, serverErrors };
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  world = await bootstrapWorld();
});

test('all remaining routes are reachable and core controls work against the real API', async ({
  page,
}) => {
  const failures = collectUnexpectedFailures(page);
  await injectSession(page, world.token);
  await page.setViewportSize({ width: 1440, height: 900 });
  await setTheme(page, 'light');

  await openReady(page, targetRoutes()[0]!);
  await page.getByTestId('projects-view-grid').click();
  await expect(page.getByTestId(`project-card-${world.projectId}`)).toBeVisible();
  await page.getByTestId('projects-view-list').click();
  await expect(page.getByTestId(`project-card-${world.projectId}`)).toBeVisible();

  await openReady(page, targetRoutes()[1]!);
  for (const tab of ['issues', 'milestones', 'updates', 'dashboard', 'overview']) {
    const control = page.getByTestId(`tab-${tab}`);
    await control.click();
    await expect(control).toHaveAttribute('aria-selected', 'true');
  }

  await openReady(page, targetRoutes()[2]!);
  const projectName = page.getByTestId('settings-name');
  const originalProjectName = await projectName.inputValue();
  await projectName.fill(`${originalProjectName} interaction draft`);
  await expect(projectName).toHaveValue(`${originalProjectName} interaction draft`);
  await projectName.fill(originalProjectName);

  await openReady(page, targetRoutes()[3]!);
  const cycleFilter = page.getByTestId('cycles-state-filter');
  await cycleFilter.selectOption('active');
  await expect(page.getByTestId(`cycle-row-${world.cycleId}`)).toBeVisible();
  await cycleFilter.selectOption('all');

  await openReady(page, targetRoutes()[4]!);
  const agentTabs = [
    ['config', 'agent-panel-config'],
    ['skills', 'agent-panel-skills'],
    ['visibility', 'agent-panel-visibility'],
    ['history', 'agent-panel-history'],
    ['overview', 'agent-panel-overview'],
  ] as const;
  for (const [tab, panel] of agentTabs) {
    await page.getByTestId(`agent-tab-${tab}`).click();
    await expect(page.getByTestId(panel)).toBeVisible();
  }
  await page.getByTestId('agent-tab-skills').click();
  await expect(page.getByTestId('agent-tools-table')).toBeVisible();
  await page.getByTestId('agent-edit-button').click();
  await page.getByTestId('agent-wizard-next').click();
  await page.getByTestId('agent-wizard-next').click();
  await expect(page.getByTestId('agent-wizard-skills')).toBeVisible();
  await expect(page.getByTestId('agent-wizard-tool-read:code')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: /close/i }).click();

  await openReady(page, targetRoutes()[5]!);
  const squadSearch = page.getByTestId('squad-filter-q');
  await squadSearch.fill('no-squad-matches-this-query');
  await expect(page.getByTestId(`squad-card-${world.squadId}`)).toBeHidden();
  await squadSearch.fill('');
  await expect(page.getByTestId(`squad-card-${world.squadId}`)).toBeVisible();
  await page.getByTestId('squad-open-create').click();
  await expect(page.getByTestId('squad-create-form')).toBeVisible();
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[6]!);
  await page.getByTestId('squad-edit-toggle').click();
  await expect(page.getByTestId('squad-edit-form')).toBeVisible();
  await page.keyboard.press('Escape');
  const activityFilter = page.getByTestId('squad-activity-filter');
  const activityOptions = await activityFilter.locator('option').allTextContents();
  expect(activityOptions.length).toBeGreaterThan(1);
  await activityFilter.selectOption({ index: 1 });
  await activityFilter.selectOption({ index: 0 });

  await openReady(page, targetRoutes()[7]!);
  const squadKanban = page.getByTestId('squad-view-kanban');
  await squadKanban.click();
  await expect(squadKanban).toHaveAttribute('aria-selected', 'true');
  const squadTree = page.getByTestId('squad-view-tree');
  await squadTree.click();
  await expect(squadTree).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('squad-task-tree-pane')).toBeVisible();

  await openReady(page, targetRoutes()[8]!);
  const autopilotSearch = page.getByTestId('autopilot-search');
  await autopilotSearch.fill('no-autopilot-matches-this-query');
  await expect(page.getByTestId(`autopilot-row-${world.autopilotId}`)).toBeHidden();
  await autopilotSearch.fill('');
  await expect(page.getByTestId(`autopilot-row-${world.autopilotId}`)).toBeVisible();

  await openReady(page, targetRoutes()[9]!);
  const autopilotName = page.getByTestId('autopilot-editor-name');
  await autopilotName.fill('Interactive draft automation');
  await expect(page.getByTestId('autopilot-summary-name')).toHaveText(
    'Interactive draft automation',
  );
  await page.getByTestId('autopilot-step-trigger').click();
  await expect(page.getByTestId('autopilot-editor-trigger-type')).toBeVisible();

  await openReady(page, targetRoutes()[10]!);
  await page.getByTestId('autopilot-detail-test-run').click();
  await expect(page.getByTestId('autopilot-test-payload')).toBeVisible();
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[11]!);
  const webhookLabel = page.getByTestId('webhook-label-input');
  await webhookLabel.fill('MES-116 interaction credential');
  await page.getByTestId('webhook-create-secret').click();
  await expect(page.getByTestId('webhook-fresh-credential')).toBeVisible();

  await openReady(page, targetRoutes()[12]!);
  const runtimeSearch = page.getByTestId('runtimes-search');
  await runtimeSearch.fill('no-runtime-matches-this-query');
  await expect(page.getByTestId(`runtime-row-${world.runtimeId}`)).toBeHidden();
  await runtimeSearch.fill('');
  await expect(page.getByTestId(`runtime-row-${world.runtimeId}`)).toBeVisible();

  await openReady(page, targetRoutes()[13]!);
  await page.getByTestId('runtime-detail-rotate').click();
  await expect(page.getByTestId('runtime-rotate-dialog')).toBeVisible();
  await expect(page.getByTestId('runtime-rotate-token')).not.toHaveText('');
  await page.getByTestId('runtime-rotate-close').click();

  await openReady(page, targetRoutes()[14]!);
  const skillSearch = page.getByTestId('skills-search');
  await skillSearch.fill('no-skill-matches-this-query');
  await expect(page.getByTestId(`skill-card-${world.skillId}`)).toBeHidden();
  await skillSearch.fill('');
  await expect(page.getByTestId(`skill-card-${world.skillId}`)).toBeVisible();
  await page.getByTestId('skills-import-open').click();
  await expect(page.getByTestId('import-uri')).toBeVisible();
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[15]!);
  const marketplaceSearch = page.getByRole('textbox').first();
  await marketplaceSearch.fill('responsive verification');
  await expect(marketplaceSearch).toHaveValue('responsive verification');
  await marketplaceSearch.fill('');

  await openReady(page, targetRoutes()[16]!);
  for (const tab of ['versions', 'scripts', 'references', 'triggers', 'overview']) {
    const control = page.getByTestId(`skill-tab-${tab}`);
    await control.click();
    await expect(control).toHaveClass(/is-active/);
  }

  await openReady(page, targetRoutes()[17]!);
  const connectButton = page.locator('[data-testid^="connector-connect-"]').last();
  await expect(connectButton).toBeVisible();
  await connectButton.click();
  await expect(page.getByTestId('integration-add-name')).toBeVisible();
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[18]!);
  const integrationTabs = [
    ['bindings', 'binding-drawer'],
    ['events', 'event-ledger'],
    ['health', 'integration-health-panel'],
    ['overview', 'integration-overview'],
  ] as const;
  for (const [tab, panel] of integrationTabs) {
    await page.getByTestId(`integration-tab-${tab}`).click();
    await expect(page.getByTestId(panel)).toBeVisible();
  }

  await openReady(page, targetRoutes()[19]!);
  await page.getByTestId(`webhook-expand-${world.subscriptionId}`).click();
  await expect(page.getByTestId(`webhook-detail-${world.subscriptionId}`)).toBeVisible();

  await openReady(page, targetRoutes()[20]!);
  await page.getByTestId('labels-create').click();
  await expect(page.getByTestId('label-name-input')).toBeVisible();
  await page.getByTestId('label-name-input').fill('Interaction draft label');
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[21]!);
  await page.getByTestId('fields-create').click();
  await expect(page.getByTestId('field-name-input')).toBeVisible();
  await page.getByTestId('field-name-input').fill('Interaction draft field');
  await page.getByTestId('field-type-select').selectOption('single_select');
  await expect(page.getByTestId('field-options-editor')).toBeVisible();
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[22]!);
  await page.getByTestId('open-export-dialog').click();
  await expect(page.getByTestId('export-format-select')).toBeVisible();
  await page.getByTestId('export-format-select').selectOption('csv');
  await page.keyboard.press('Escape');

  await openReady(page, targetRoutes()[23]!);
  await page.getByTestId('notfound-home').click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('home-dashboard')).toBeVisible();

  await openReady(page, targetRoutes()[24]!);
  await page.getByTestId('oauth-callback-back').click();
  // The recovery target is /login; an already authenticated user is then
  // intentionally forwarded to the product home instead of seeing sign-in.
  await expect(page.getByTestId('home-dashboard')).toBeVisible();

  // Finish with the highest-risk write path so every other page family is
  // exercised even when the tool contract regresses. The controlled inputs
  // update only after PATCH + list reload, and the direct API read proves the
  // visible state is durable rather than an optimistic-only UI change.
  await openReady(page, targetRoutes()[4]!);
  await page.getByTestId('agent-tab-skills').click();
  const enabledTool = page.getByTestId('agent-tool-enabled-exec:shell');
  const toolPermission = page.getByTestId('agent-tool-permission-exec:shell');
  await expect(enabledTool).toBeChecked();
  await toolPermission.selectOption('read_only');
  await expect(toolPermission).toHaveValue('read_only');
  await enabledTool.click();
  await expect(enabledTool).not.toBeChecked();
  await expect
    .poll(async () => {
      const tools = await request<
        Array<{ capability: string; enabled: boolean; permission: string }>
      >('GET', `/api/v1/agents/${world.agentId}/tools`);
      const saved = tools.find((tool) => tool.capability === 'exec:shell');
      return saved === undefined
        ? null
        : { capability: saved.capability, enabled: saved.enabled, permission: saved.permission };
    })
    .toEqual({ capability: 'exec:shell', enabled: false, permission: 'read_only' });

  expect(failures.pageErrors).toEqual([]);
  expect(failures.serverErrors).toEqual([]);
});

test('all remaining routes keep document overflow contained at 320 CSS px', async ({ page }) => {
  const failures = collectUnexpectedFailures(page);
  await injectSession(page, world.token);
  await page.setViewportSize({ width: 320, height: 700 });
  await setTheme(page, 'dark');
  for (const route of targetRoutes()) {
    await openReady(page, route);
    await expectNoDocumentOverflow(page);
  }
  expect(failures.pageErrors).toEqual([]);
  expect(failures.serverErrors).toEqual([]);
});

const VISUAL_MATRIX = [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, theme: 'light' },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, theme: 'dark' },
  { name: 'tablet-light', viewport: { width: 768, height: 1024 }, theme: 'light' },
  { name: 'tablet-dark', viewport: { width: 768, height: 1024 }, theme: 'dark' },
  { name: 'mobile-light', viewport: { width: 390, height: 844 }, theme: 'light' },
  { name: 'mobile-dark', viewport: { width: 390, height: 844 }, theme: 'dark' },
] as const;

for (const scenario of VISUAL_MATRIX) {
  test(`25-page visual evidence: ${scenario.name}`, async ({ page }) => {
    const failures = collectUnexpectedFailures(page);
    const scenarioDir = resolve(EVIDENCE_DIR, 'matrix', scenario.name);
    mkdirSync(scenarioDir, { recursive: true });
    await injectSession(page, world.token);
    await page.setViewportSize(scenario.viewport);
    await setTheme(page, scenario.theme);

    for (const route of targetRoutes()) {
      await openReady(page, route);
      await prepareEvidenceState(page, route);
      await expectNoDocumentOverflow(page);
      await page.screenshot({
        path: resolve(scenarioDir, `${route.key}.png`),
        animations: 'disabled',
        fullPage: true,
      });
    }

    expect(failures.pageErrors).toEqual([]);
    expect(failures.serverErrors).toEqual([]);
  });
}
