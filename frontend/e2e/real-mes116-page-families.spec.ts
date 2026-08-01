/**
 * MES-116 real-browser acceptance for the remaining platform/admin families.
 * Data is created through the real API, then every target surface is rendered
 * and operated in Chromium. The 320px pass asserts that wide tables remain in
 * their own overflow boundary rather than widening the document.
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { injectSession } from './helpers';

const API_BASE = process.env.MES116_API_BASE ?? 'http://127.0.0.1:8000';
const EVIDENCE_DIR = resolve('e2e', 'evidence', 'mes116');
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
  const password = `Mm6!${randomBytes(24).toString('base64url')}`;
  const register = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: 'MES-116 Owner' }),
  });
  if (register.status !== 201) {
    throw new Error(`register -> ${register.status}: ${await register.text()}`);
  }
  const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
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
    { path: '/projects', testId: 'projects-view' },
    { path: `/projects/${world.projectId}`, testId: 'project-detail-header' },
    { path: `/projects/${world.projectId}/settings`, testId: 'settings-form' },
    { path: '/cycles', testId: `cycle-row-${world.cycleId}` },
    { path: `/agents/${world.agentId}`, testId: 'agent-detail-page' },
    { path: '/squads', testId: 'squad-grid' },
    { path: `/squads/${world.squadId}`, testId: 'squad-detail-page' },
    {
      path: `/squads/${world.squadId}/tasks/${world.taskId}`,
      testId: 'squad-task-page',
    },
    { path: '/autopilots', testId: 'autopilots-page' },
    { path: '/autopilots/new', testId: 'autopilot-editor' },
    { path: `/autopilots/${world.autopilotId}`, testId: 'autopilot-detail' },
    { path: '/webhooks', testId: 'webhook-config-page' },
    { path: '/runtimes', testId: 'runtimes-table' },
    { path: `/runtimes/${world.runtimeId}`, testId: 'runtime-detail-page' },
    { path: '/skills', testId: 'skills-page-title' },
    { path: '/skills/marketplace', testId: 'marketplace-title' },
    { path: `/skills/${world.skillId}`, testId: 'skill-detail' },
    { path: '/integrations', testId: 'integrations-page' },
    { path: `/integrations/${world.integrationId}`, testId: 'integration-detail' },
    {
      path: '/webhook-subscriptions',
      testId: `webhook-card-${world.subscriptionId}`,
    },
    {
      path: `/w/${world.workspaceSlug}/settings/labels`,
      testId: 'labels-panel',
    },
    {
      path: `/w/${world.workspaceSlug}/settings/custom-fields`,
      testId: 'custom-fields-panel',
    },
    {
      path: `/w/${world.workspaceSlug}/settings/data`,
      testId: 'data-management-section',
    },
    { path: '/this-route-does-not-exist', testId: 'notfound-home' },
    { path: '/auth/oauth/callback/github', testId: 'oauth-callback-error' },
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
  await expect(page.getByTestId(route.testId).first()).toBeVisible();
  // The checklist is loaded asynchronously after the target page. Dismiss it
  // after readiness so screenshots cannot accidentally capture the overlay.
  await dismissOnboarding(page);
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
  page.on('pageerror', (error) => pageErrors.push(error.message));
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

test.afterAll(async () => {
  if (token === '' || world === undefined) return;
  await request('DELETE', `/api/v1/workspaces/${world.workspaceId}`, {
    confirm_slug: world.workspaceSlug,
  });
  await request('POST', '/api/v1/auth/logout-all', {});
  token = '';
});

test('all remaining routes are reachable and core controls work against the real API', async ({
  page,
}) => {
  const failures = collectUnexpectedFailures(page);
  await injectSession(page, world.token);
  await page.setViewportSize({ width: 1440, height: 900 });
  await setTheme(page, 'light');

  for (const route of targetRoutes()) await openReady(page, route);

  await openReady(page, { path: '/projects', testId: 'projects-view' });
  await page.getByTestId('projects-view-grid').click();
  await expect(page.getByTestId(`project-card-${world.projectId}`)).toBeVisible();
  await page.getByTestId('projects-view-list').click();

  await openReady(page, {
    path: `/projects/${world.projectId}`,
    testId: 'project-detail-header',
  });
  await page.getByTestId('tab-issues').click();
  await expect(page.getByTestId('project-issue-list')).toBeVisible();
  await page.getByTestId('tab-overview').click();

  await openReady(page, { path: `/agents/${world.agentId}`, testId: 'agent-detail-page' });
  await page.getByTestId('agent-tab-skills').click();
  await expect(page.getByTestId('agent-tools-table')).toBeVisible();
  await expect(page.getByTestId('agent-tool-permission-read:code')).toBeVisible();
  await page.getByTestId('agent-edit-button').click();
  await page.getByTestId('agent-wizard-next').click();
  await page.getByTestId('agent-wizard-next').click();
  await expect(page.getByTestId('agent-wizard-skills')).toBeVisible();
  await expect(page.getByTestId('agent-wizard-tool-read:code')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: /close/i }).click();

  await openReady(page, {
    path: `/squads/${world.squadId}/tasks/${world.taskId}`,
    testId: 'squad-task-page',
  });
  await page.getByTestId('squad-view-kanban').click();
  await page.getByTestId('squad-view-tree').click();

  await openReady(page, {
    path: `/integrations/${world.integrationId}`,
    testId: 'integration-detail',
  });
  await page.getByTestId('integration-tab-health').click();
  await expect(page.getByTestId('integration-health-panel')).toBeVisible();

  await openReady(page, {
    path: '/webhook-subscriptions',
    testId: `webhook-card-${world.subscriptionId}`,
  });
  await page.getByTestId(`webhook-expand-${world.subscriptionId}`).click();
  await expect(page.getByTestId(`webhook-detail-${world.subscriptionId}`)).toBeVisible();

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

test('desktop/tablet/mobile light/dark visual evidence matrix', async ({ page }) => {
  const failures = collectUnexpectedFailures(page);
  await injectSession(page, world.token);
  const shot = async (
    name: string,
    viewport: { width: number; height: number },
    theme: 'light' | 'dark',
    route: ReadyRoute,
    prepare?: () => Promise<void>,
  ): Promise<void> => {
    await page.setViewportSize(viewport);
    await setTheme(page, theme);
    await openReady(page, route);
    if (prepare !== undefined) await prepare();
    await expectNoDocumentOverflow(page);
    await page.screenshot({
      path: resolve(EVIDENCE_DIR, `${name}.png`),
      animations: 'disabled',
    });
  };

  await shot('01-desktop-light-project-detail', { width: 1440, height: 900 }, 'light', {
    path: `/projects/${world.projectId}`,
    testId: 'project-detail-header',
  });
  await shot(
    '02-desktop-dark-agent-effective-tools',
    { width: 1440, height: 900 },
    'dark',
    { path: `/agents/${world.agentId}`, testId: 'agent-detail-page' },
    async () => {
      await page.getByTestId('agent-tab-skills').click();
      await expect(page.getByTestId('agent-tools-table')).toBeVisible();
    },
  );
  await shot('03-tablet-light-squad-detail', { width: 768, height: 1024 }, 'light', {
    path: `/squads/${world.squadId}`,
    testId: 'squad-detail-page',
  });
  await shot(
    '04-tablet-dark-autopilot-editor',
    { width: 768, height: 1024 },
    'dark',
    { path: '/autopilots/new', testId: 'autopilot-editor' },
    async () => {
      await expect(page.getByTestId('autopilot-editor-summary')).toBeVisible();
    },
  );
  await shot('05-mobile-light-runtime-detail', { width: 390, height: 844 }, 'light', {
    path: `/runtimes/${world.runtimeId}`,
    testId: 'runtime-detail-page',
  });
  await shot(
    '06-mobile-dark-integration-health',
    { width: 390, height: 844 },
    'dark',
    { path: `/integrations/${world.integrationId}`, testId: 'integration-detail' },
    async () => {
      await page.getByTestId('integration-tab-health').click();
      await expect(page.getByTestId('integration-health-panel')).toBeVisible();
      const tabsFit = await page.getByTestId('integration-tabs').evaluate((tablist) => {
        const boundary = tablist.getBoundingClientRect();
        return [...tablist.children].every((tab) => {
          const bounds = tab.getBoundingClientRect();
          return bounds.left >= boundary.left - 1 && bounds.right <= boundary.right + 1;
        });
      });
      expect(tabsFit).toBe(true);
    },
  );

  expect(failures.pageErrors).toEqual([]);
  expect(failures.serverErrors).toEqual([]);
});
