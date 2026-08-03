/**
 * MES-160 communications and management real-stack acceptance.
 *
 * No route interception or fixture data is used. Every browser/API operation
 * crosses the same-origin nginx front door into FastAPI, PostgreSQL, Redis,
 * the realtime gateway, and the worker. The first test additionally proves
 * persisted tenant boundaries and the private realtime channels.
 */
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page, TestInfo } from '@playwright/test';

const PG_CONTAINER = process.env.MES160_PG_CONTAINER ?? 'mes160-real-postgres-1';
const RESPONSE_TIMEOUT = 30_000;
const REALTIME_TIMEOUT = 45_000;
const EVIDENCE_DIR = join(dirname(fileURLToPath(import.meta.url)), 'evidence', 'mes160');

interface Envelope<T> {
  readonly data: T;
}

interface Workspace {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
}

interface Agent {
  readonly id: string;
  readonly name: string;
  readonly lifecycle_status: string;
}

interface Member {
  readonly id: string;
  readonly member_type: 'human' | 'agent';
  readonly display_name: string;
  readonly profile: null | {
    readonly id: string;
    readonly email?: string;
  };
}

interface Notification {
  readonly id: string;
  readonly read_at: string | null;
}

interface GenerationStart {
  readonly message_id: string;
  readonly generation_id: string;
  readonly stream_url: string;
}

interface World {
  readonly ownerEmail: string;
  readonly ownerPassword: string;
  readonly ownerToken: string;
  readonly peerToken: string;
  readonly workspaceA: Workspace;
  readonly workspaceB: Workspace;
  readonly agentA: Agent;
  readonly agentB: Agent;
  readonly agentMemberB: Member;
  readonly ownerMemberB: Member;
  readonly issueB: { readonly id: string };
}

interface DbEvidence {
  readonly owner_memberships: number;
  readonly workspace_a_agents: number;
  readonly workspace_b_agents: number;
  readonly notification_in_b: number;
  readonly notification_is_read: boolean;
  readonly chat_sessions_in_a: number;
  readonly chat_sessions_in_b: number;
  readonly chat_message_count: number;
}

interface FrameLog {
  readonly sent: string[];
  readonly received: string[];
}

function sqlLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

function psqlJson<T>(sql: string): T {
  const output = execFileSync(
    'docker',
    [
      'exec',
      '-i',
      PG_CONTAINER,
      'psql',
      '-U',
      'mesh',
      '-d',
      'mesh',
      '-v',
      'ON_ERROR_STOP=1',
      '-tAc',
      sql,
    ],
    { encoding: 'utf8', timeout: 30_000 },
  ).trim();
  return JSON.parse(output) as T;
}

async function dataOf<T>(response: { json(): Promise<unknown> }): Promise<T> {
  return ((await response.json()) as Envelope<T>).data;
}

async function postData<T>(
  request: APIRequestContext,
  path: string,
  token: string,
  data: Record<string, unknown>,
  expectedStatus = 201,
): Promise<T> {
  const response = await request.post(path, {
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(expectedStatus);
  return dataOf<T>(response);
}

async function getData<T>(request: APIRequestContext, path: string, token: string): Promise<T> {
  const response = await request.get(path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(200);
  return dataOf<T>(response);
}

async function registerAndToken(
  request: APIRequestContext,
  email: string,
  password: string,
  displayName: string,
): Promise<string> {
  const registration = await request.post('/api/v1/auth/register', {
    data: { email, password, display_name: displayName },
  });
  expect([200, 201]).toContain(registration.status());
  const login = await request.post('/api/v1/auth/login', { data: { email, password } });
  expect(login.status(), await login.text()).toBe(200);
  return (await dataOf<{ access_token: string }>(login)).access_token;
}

async function seedWorld(request: APIRequestContext, label: string): Promise<World> {
  const suffix = `${label}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
    .replaceAll('_', '-')
    .toLowerCase();
  const ownerEmail = `mes160-owner-${suffix}@example.com`;
  const peerEmail = `mes160-peer-${suffix}@example.com`;
  const ownerPassword = `Mesh#${suffix}A9!`;
  const peerPassword = `Mesh#Peer-${suffix}B8!`;
  const ownerToken = await registerAndToken(
    request,
    ownerEmail,
    ownerPassword,
    `MES-160 Owner ${label}`,
  );
  const peerToken = await registerAndToken(
    request,
    peerEmail,
    peerPassword,
    `MES-160 Peer ${label}`,
  );

  // A is deliberately created first. Every canonical B page must still use
  // WorkspaceProvider's B identity instead of memberships[0].
  const slugA = `m160-a-${suffix}`.slice(0, 32);
  const slugB = `m160-b-${suffix}`.slice(0, 32);
  const workspaceA = await postData<Workspace>(request, '/api/v1/workspaces', ownerToken, {
    name: `MES-160 Alpha ${label}`,
    slug: slugA,
  });
  const workspaceB = await postData<Workspace>(request, '/api/v1/workspaces', ownerToken, {
    name: `MES-160 Beta ${label}`,
    slug: slugB,
  });
  const agentA = await postData<Agent>(
    request,
    `/api/v1/workspaces/${workspaceA.id}/agents`,
    ownerToken,
    { name: `Alpha Agent ${suffix}` },
  );
  const agentB = await postData<Agent>(
    request,
    `/api/v1/workspaces/${workspaceB.id}/agents`,
    ownerToken,
    { name: `Beta Agent ${suffix}` },
  );

  const invitation = await postData<readonly { readonly invite_link: string }[]>(
    request,
    `/api/v1/workspaces/${workspaceB.id}/invitations`,
    ownerToken,
    { emails: [peerEmail], role: 'member' },
  );
  const invitationToken = invitation[0]?.invite_link.split('/').pop();
  expect(invitationToken).toBeTruthy();
  await postData(request, '/api/v1/invitations/accept', peerToken, { token: invitationToken }, 200);

  const membersB = await getData<readonly Member[]>(
    request,
    `/api/v1/workspaces/${workspaceB.id}/members?limit=100`,
    ownerToken,
  );
  const ownerMemberB = membersB.find(
    (member) => member.member_type === 'human' && member.profile?.email === ownerEmail,
  );
  const agentMemberB = membersB.find(
    (member) => member.member_type === 'agent' && member.profile?.id === agentB.id,
  );
  expect(ownerMemberB, 'owner roster identity in workspace B').toBeDefined();
  expect(agentMemberB, 'agent roster identity in workspace B').toBeDefined();
  const issueB = await postData<{ readonly id: string }>(
    request,
    `/api/v1/workspaces/${workspaceB.id}/issues`,
    ownerToken,
    { title: `MES-160 realtime issue ${suffix}` },
  );

  return {
    ownerEmail,
    ownerPassword,
    ownerToken,
    peerToken,
    workspaceA,
    workspaceB,
    agentA,
    agentB,
    agentMemberB: agentMemberB as Member,
    ownerMemberB: ownerMemberB as Member,
    issueB,
  };
}

async function loginThroughUi(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(password);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/auth/login',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('login-account-submit').click();
  expect((await responsePromise).status()).toBe(200);
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
}

function observeWebSockets(page: Page): FrameLog {
  const log: FrameLog = { sent: [], received: [] };
  page.on('websocket', (socket) => {
    socket.on('framesent', ({ payload }) => {
      log.sent.push(typeof payload === 'string' ? payload : payload.toString('utf8'));
    });
    socket.on('framereceived', ({ payload }) => {
      log.received.push(typeof payload === 'string' ? payload : payload.toString('utf8'));
    });
  });
  return log;
}

async function expectFrame(frames: readonly string[], ...parts: readonly string[]): Promise<void> {
  await expect
    .poll(() => frames.some((frame) => parts.every((part) => frame.includes(part))), {
      timeout: REALTIME_TIMEOUT,
    })
    .toBe(true);
}

async function setAndAssertTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  if (theme === 'dark') {
    await page.goto('/settings/appearance');
    const responsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'PATCH' &&
        new URL(response.url()).pathname === '/api/v1/users/me',
      { timeout: RESPONSE_TIMEOUT },
    );
    await page.getByTestId('theme-select').selectOption('dark');
    expect((await responsePromise).status()).toBe(200);
  }
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${theme}\\b`));
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

async function capture(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const body = await page.screenshot({ fullPage: true });
  await testInfo.attach(name, { body, contentType: 'image/png' });
  if (name === 'members') {
    await mkdir(EVIDENCE_DIR, { recursive: true });
    await writeFile(join(EVIDENCE_DIR, `${testInfo.project.name}-members.png`), body);
  }
}

function waitForGet(page: Page, pathname: string): Promise<void> {
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === 'GET' && new URL(candidate.url()).pathname === pathname,
    { timeout: RESPONSE_TIMEOUT },
  );
  return response.then((result) => {
    expect(result.status(), `${pathname}: ${result.status()}`).toBe(200);
  });
}

test.describe.configure({ mode: 'serial' });

test('canonical B communications stay tenant-scoped and merge real realtime events', async ({
  page,
  request,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-light', 'one production journey is sufficient');
  const frames = observeWebSockets(page);
  const world = await seedWorld(request, 'core');
  await loginThroughUi(page, world.ownerEmail, world.ownerPassword);
  await setAndAssertTheme(page, 'light');

  const membersGet = waitForGet(page, `/api/v1/workspaces/${world.workspaceB.id}/members`);
  await page.goto(`/w/${world.workspaceB.slug}/members`);
  await membersGet;
  await expect(page.getByTestId('data-view')).toBeVisible();
  await expect(page.getByTestId(`member-row-${world.agentMemberB.id}`)).toContainText(
    world.agentB.name,
  );
  await expect(page.getByText(world.agentA.name, { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('tab-all')).toHaveAttribute('data-slot', 'tabs-trigger');

  await expectFrame(frames.sent, `workspace:${world.workspaceB.id}`);
  const liveAgentName = `Live Agent ${Date.now().toString(36)}`;
  await postData<Agent>(
    request,
    `/api/v1/workspaces/${world.workspaceB.id}/agents`,
    world.ownerToken,
    { name: liveAgentName },
  );
  await expectFrame(frames.received, 'member.added');
  await expect(
    page.locator('[data-testid^="member-row-"]').filter({ hasText: liveAgentName }).first(),
  ).toBeVisible({
    timeout: REALTIME_TIMEOUT,
  });

  const betaRow = page
    .locator('[data-testid^="member-row-"]')
    .filter({ hasText: world.agentB.name })
    .first();
  await betaRow.locator('[data-testid^="member-open-"]').click();
  await expect(page).toHaveURL(
    new RegExp(`/w/${world.workspaceB.slug}/agents/${world.agentB.id}(?:\\?|$)`),
  );
  await expect(page.getByTestId('agent-detail-page')).toBeVisible();
  await expectFrame(frames.sent, `workspace:${world.workspaceB.id}:agents`);
  const priorStatus = (await page.getByTestId('agent-detail-status').textContent()) ?? '';
  await postData<Agent>(
    request,
    `/api/v1/workspaces/${world.workspaceB.id}/agents/${world.agentB.id}:pause`,
    world.ownerToken,
    { reason: 'MES-160 realtime acceptance', in_flight_policy: 'finish_current' },
    200,
  );
  await expectFrame(frames.received, 'agent.lifecycle_changed', world.agentB.id);
  await expect(page.getByTestId('agent-detail-status')).not.toHaveText(priorStatus, {
    timeout: REALTIME_TIMEOUT,
  });
  const paused = await getData<Agent>(
    request,
    `/api/v1/workspaces/${world.workspaceB.id}/agents/${world.agentB.id}`,
    world.ownerToken,
  );
  expect(paused.lifecycle_status).toBe('paused');
  await postData<Agent>(
    request,
    `/api/v1/workspaces/${world.workspaceB.id}/agents/${world.agentB.id}:resume`,
    world.ownerToken,
    {},
    200,
  );

  const inboxGet = waitForGet(page, '/api/v1/inbox');
  await page.goto(`/w/${world.workspaceB.slug}/inbox`);
  await inboxGet;
  await expect(page.getByTestId('inbox-page')).toBeVisible();
  await expectFrame(frames.sent, `member:${world.ownerMemberB.id}:inbox`);
  const comment = await postData<{ readonly id: string }>(
    request,
    `/api/v1/issues/${world.issueB.id}/comments`,
    world.peerToken,
    { body_markdown: `MES-160 realtime comment ${Date.now().toString(36)}` },
  );
  expect(comment.id).toBeTruthy();
  await expectFrame(frames.received, 'notification.created');

  let notification: Notification | undefined;
  await expect
    .poll(
      async () => {
        const list = await getData<readonly Notification[]>(
          request,
          `/api/v1/inbox?workspace_id=${world.workspaceB.id}&limit=50`,
          world.ownerToken,
        );
        notification = list.find((item) => item.read_at === null);
        return notification?.id ?? null;
      },
      { timeout: REALTIME_TIMEOUT },
    )
    .not.toBeNull();
  const notificationId = (notification as Notification).id;
  await expect(page.getByTestId(`inbox-row-${notificationId}`)).toBeVisible({
    timeout: REALTIME_TIMEOUT,
  });
  const markReadResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/inbox/${notificationId}/read`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId(`inbox-mark-read-${notificationId}`).click();
  expect((await markReadResponse).status()).toBe(200);
  await expect(page.getByTestId(`inbox-unread-dot-${notificationId}`)).toHaveCount(0);

  const chatListGet = waitForGet(page, `/api/v1/workspaces/${world.workspaceB.id}/chat-sessions`);
  await page.goto(`/w/${world.workspaceB.slug}/chat`);
  await chatListGet;
  await expect(page.getByTestId('chat-page')).toBeVisible();
  await expect(page.locator('li.mesh-chat__session')).toHaveCount(0);
  await expectFrame(frames.sent, `chat_list:${world.ownerMemberB.id}`);
  await page.getByTestId('chat-new-session').click();
  await page.getByTestId('chat-new-session-agent').selectOption(world.agentB.id);
  const createSessionResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname ===
        `/api/v1/workspaces/${world.workspaceB.id}/chat-sessions`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('chat-new-session-create').click();
  const sessionResponse = await createSessionResponse;
  expect(sessionResponse.status()).toBe(201);
  const session = await dataOf<{ readonly id: string }>(sessionResponse);
  await expect(page).toHaveURL(
    new RegExp(`/w/${world.workspaceB.slug}/chat/${session.id}(?:\\?|$)`),
  );
  const sessionPath = `/api/v1/workspaces/${world.workspaceB.id}/chat-sessions/${session.id}`;
  await page.evaluate(() => {
    type CaptureWindow = Window & { __mes160SseCapture?: string };
    const captureWindow = window as CaptureWindow;
    captureWindow.__mes160SseCapture = '';
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args): Promise<Response> => {
      const response = await originalFetch(...args);
      const pathname = new URL(response.url).pathname;
      if (pathname.includes('/generations/') && pathname.endsWith('/stream')) {
        const reader = response.clone().body?.getReader();
        if (reader !== undefined) {
          void (async () => {
            const decoder = new TextDecoder();
            try {
              while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                captureWindow.__mes160SseCapture =
                  (captureWindow.__mes160SseCapture ?? '') +
                  decoder.decode(value, { stream: true });
              }
            } finally {
              captureWindow.__mes160SseCapture =
                (captureWindow.__mes160SseCapture ?? '') + decoder.decode();
            }
          })().catch(() => undefined);
        }
      }
      return response;
    };
  });
  const sendMessageResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `${sessionPath}/messages`,
    { timeout: RESPONSE_TIMEOUT },
  );
  const streamResponse = page.waitForResponse(
    (response) => {
      const pathname = new URL(response.url()).pathname;
      return (
        response.request().method() === 'GET' &&
        pathname.startsWith(`${sessionPath}/generations/`) &&
        pathname.endsWith('/stream')
      );
    },
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('chat-composer-input').fill('Reply with one short sentence.');
  await page.getByTestId('chat-composer-send').click();
  const generationResponse = await sendMessageResponse;
  expect(generationResponse.status()).toBe(201);
  const generation = await dataOf<GenerationStart>(generationResponse);
  const expectedStreamPath = `${sessionPath}/generations/${generation.generation_id}/stream`;
  expect(generation.stream_url).toBe(expectedStreamPath);
  const observedStreamResponse = await streamResponse;
  expect(new URL(observedStreamResponse.url()).pathname).toBe(expectedStreamPath);
  expect(observedStreamResponse.status()).toBe(200);
  expect(await observedStreamResponse.headerValue('content-type')).toContain('text/event-stream');
  await page.waitForFunction(
    () => {
      const bodies = Array.from(document.querySelectorAll('[data-testid^="chat-body-"]'));
      return bodies.length >= 2 && (bodies.at(-1)?.textContent ?? '').trim().length > 0;
    },
    undefined,
    { timeout: 60_000 },
  );
  await page.waitForFunction(
    () => {
      const streamBody =
        (window as Window & { __mes160SseCapture?: string }).__mes160SseCapture ?? '';
      return streamBody.replaceAll('\r\n', '\n').includes('event: message.delta\n');
    },
    undefined,
    { timeout: 60_000 },
  );
  const streamBody = (
    await page.evaluate(
      () => (window as Window & { __mes160SseCapture?: string }).__mes160SseCapture ?? '',
    )
  ).replaceAll('\r\n', '\n');
  const deltaFrames = streamBody
    .split('\n\n')
    .filter((block) => block.split('\n').includes('event: message.delta'));
  expect(deltaFrames.length).toBeGreaterThan(0);
  expect(deltaFrames[0]).toMatch(/^id: [1-9]\d*$/m);
  expect(deltaFrames[0]).toMatch(/^data: \{.*"delta":\s*".+".*\}$/m);

  const database = psqlJson<DbEvidence>(`
    SELECT json_build_object(
      'owner_memberships', (
        SELECT count(*) FROM members m
        JOIN users u ON u.id = m.user_id
        WHERE u.email = ${sqlLiteral(world.ownerEmail)}
          AND m.status = 'active'
      ),
      'workspace_a_agents', (
        SELECT count(*) FROM agents
        WHERE workspace_id = ${sqlLiteral(world.workspaceA.id)}::uuid
          AND name = ${sqlLiteral(world.agentA.name)}
          AND deleted_at IS NULL
      ),
      'workspace_b_agents', (
        SELECT count(*) FROM agents
        WHERE workspace_id = ${sqlLiteral(world.workspaceB.id)}::uuid
          AND deleted_at IS NULL
      ),
      'notification_in_b', (
        SELECT count(*) FROM notifications
        WHERE id = ${sqlLiteral(notificationId)}::uuid
          AND workspace_id = ${sqlLiteral(world.workspaceB.id)}::uuid
      ),
      'notification_is_read', (
        SELECT read_at IS NOT NULL FROM notifications
        WHERE id = ${sqlLiteral(notificationId)}::uuid
      ),
      'chat_sessions_in_a', (
        SELECT count(*) FROM chat_sessions
        WHERE id = ${sqlLiteral(session.id)}::uuid
          AND workspace_id = ${sqlLiteral(world.workspaceA.id)}::uuid
      ),
      'chat_sessions_in_b', (
        SELECT count(*) FROM chat_sessions
        WHERE id = ${sqlLiteral(session.id)}::uuid
          AND workspace_id = ${sqlLiteral(world.workspaceB.id)}::uuid
      ),
      'chat_message_count', (
        SELECT message_count FROM chat_sessions
        WHERE id = ${sqlLiteral(session.id)}::uuid
      )
    );
  `);
  expect(database.owner_memberships).toBe(2);
  expect(database.workspace_a_agents).toBe(1);
  expect(database.workspace_b_agents).toBeGreaterThanOrEqual(2);
  expect(database.notification_in_b).toBe(1);
  expect(database.notification_is_read).toBe(true);
  expect(database.chat_sessions_in_a).toBe(0);
  expect(database.chat_sessions_in_b).toBe(1);
  expect(database.chat_message_count).toBeGreaterThanOrEqual(2);
  await expectNoHorizontalOverflow(page);
});

test('communications and management matrix renders real light/dark desktop/mobile pages', async ({
  page,
  request,
}, testInfo) => {
  const world = await seedWorld(request, testInfo.project.name);
  await loginThroughUi(page, world.ownerEmail, world.ownerPassword);
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  await setAndAssertTheme(page, theme);

  const membersGet = waitForGet(page, `/api/v1/workspaces/${world.workspaceB.id}/members`);
  await page.goto(`/w/${world.workspaceB.slug}/members`);
  await membersGet;
  const rosterEntry = testInfo.project.name.startsWith('phone')
    ? page.getByTestId(`member-card-${world.agentMemberB.id}`)
    : page.getByTestId(`member-row-${world.agentMemberB.id}`);
  await expect(rosterEntry).toContainText(world.agentB.name);
  await expect(page.getByText(world.agentA.name, { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('data-view')).toHaveClass(/mesh-members/);
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'members');

  const inboxGet = waitForGet(page, '/api/v1/inbox');
  await page.goto(`/w/${world.workspaceB.slug}/inbox`);
  await inboxGet;
  await expect(page.getByTestId('inbox-page')).toBeVisible();
  await expect(page.getByTestId('inbox-tab-all')).toHaveAttribute('data-slot', 'tabs-trigger');
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'inbox');

  const chatGet = waitForGet(page, `/api/v1/workspaces/${world.workspaceB.id}/chat-sessions`);
  await page.goto(`/w/${world.workspaceB.slug}/chat`);
  await chatGet;
  await expect(page.getByTestId('chat-page')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'chat');

  await page.goto(`/w/${world.workspaceB.slug}/settings/general`);
  await expect(page.getByTestId('ws-name-input')).toHaveValue(world.workspaceB.name, {
    timeout: RESPONSE_TIMEOUT,
  });
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'workspace-settings');

  const managementPages = [
    {
      name: 'runtimes',
      path: `/w/${world.workspaceB.slug}/automations/runtimes`,
      api: `/api/v1/workspaces/${world.workspaceB.id}/runtimes`,
      selector: 'runtimes-search',
    },
    {
      name: 'skills',
      path: `/w/${world.workspaceB.slug}/automations/skills`,
      api: `/api/v1/workspaces/${world.workspaceB.id}/skills`,
      selector: 'skills-search',
    },
    {
      name: 'squads',
      path: `/w/${world.workspaceB.slug}/squads`,
      api: `/api/v1/workspaces/${world.workspaceB.id}/squads`,
      selector: 'squad-filter-q',
    },
    {
      name: 'autopilots',
      path: `/w/${world.workspaceB.slug}/automations/autopilots`,
      api: `/api/v1/workspaces/${world.workspaceB.id}/autopilots`,
      selector: 'autopilots-page',
    },
  ] as const;

  for (const management of managementPages) {
    const apiGet = waitForGet(page, management.api);
    await page.goto(management.path);
    await apiGet;
    await expect(page.getByTestId(management.selector)).toBeVisible();
    await expect(page.getByTestId('data-view')).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, management.name);
  }
});
