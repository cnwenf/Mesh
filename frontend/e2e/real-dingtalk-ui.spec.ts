/**
 * MES-90 DingTalk frontend acceptance against the real Mesh API.
 *
 * Prerequisite: the compose API/worker/gateway stack is running on 8000/8081.
 * Playwright starts the current Vite frontend through playwright.real.config.ts.
 * No route is mocked: integration creation/editing, binding, signed inbound
 * callbacks, queue reads/cancellation, and the outbound failure diagnostic all
 * hit the real backend and database. Fake DingTalk credentials intentionally
 * exercise the real `upstream_error` path because this test owns no enterprise
 * DingTalk application.
 */
import { createHmac } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, APIResponse, Locator, Page } from '@playwright/test';
import { injectSession } from './helpers';

const HERE = dirname(fileURLToPath(import.meta.url));
const API_BASE = process.env.MES90_API_BASE ?? 'http://127.0.0.1:8000';
const EVIDENCE_DIR = process.env.MES90_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'mes90-dingtalk');

interface World {
  readonly token: string;
  readonly memberToken: string;
  readonly workspaceId: string;
  readonly agentId: string;
  readonly privateProjectId: string;
  readonly appKey: string;
  readonly appSecret: string;
  readonly corpId: string;
  readonly conversationId: string;
  readonly privateConversationId: string;
  readonly senderStaffId: string;
  readonly ownerStaffId: string;
}

interface QueueItem {
  readonly id: string;
  readonly state: string;
  readonly position: number | null;
  readonly message_excerpt: string;
  readonly execution_id: string | null;
}

interface ClaimedExecution {
  readonly executionId: string;
  readonly attemptId: string;
  readonly leaseSeq: number;
}

interface QueueSummary {
  readonly conversation_key: string;
  readonly pending_count: number;
}

interface IntegrationCreateResponse {
  readonly data: {
    readonly integration: {
      readonly id: string;
      readonly config: Readonly<Record<string, unknown>>;
      readonly has_secret: boolean;
    };
    readonly secret_accepted: boolean;
  };
}

type OutboxPayload = Readonly<Record<string, unknown>>;

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

function runPostgresQuery(sql: string): string {
  const user = process.env.MES90_PG_USER ?? 'mesh';
  const database = process.env.MES90_PG_DATABASE ?? 'mesh';
  const host = process.env.MES90_PG_HOST;
  if (host !== undefined) {
    const password = process.env.MES90_PG_PASSWORD;
    expect(password, 'MES90_PG_PASSWORD is required with MES90_PG_HOST').toBeTruthy();
    return execFileSync(
      'psql',
      [
        '-X',
        '-v',
        'ON_ERROR_STOP=1',
        '-h',
        host,
        '-p',
        process.env.MES90_PG_PORT ?? '5432',
        '-U',
        user,
        '-d',
        database,
        '-tA',
        '-c',
        sql,
      ],
      { encoding: 'utf8', env: { ...process.env, PGPASSWORD: password } },
    );
  }

  return execFileSync(
    'docker',
    [
      'exec',
      '-i',
      process.env.MES90_PG_CONTAINER ?? 'mesh-postgres-1',
      'psql',
      '-X',
      '-v',
      'ON_ERROR_STOP=1',
      '-U',
      user,
      '-d',
      database,
      '-tA',
      '-c',
      sql,
    ],
    { encoding: 'utf8' },
  );
}

function readImSendPayloads(workspaceId: string): OutboxPayload[] {
  if (!/^[0-9a-f-]{36}$/i.test(workspaceId)) throw new Error('invalid workspace id');
  const rows = runPostgresQuery(
    `SELECT payload::text FROM outbox_events WHERE workspace_id = '${workspaceId}' AND event_type = 'im.send' ORDER BY created_at, id`,
  ).trim();
  if (rows === '') return [];
  return rows.split('\n').map((row) => JSON.parse(row) as OutboxPayload);
}

async function expectJsonStatus(response: APIResponse, expected: number): Promise<unknown> {
  const body = await response.json().catch(() => null);
  expect(response.status(), JSON.stringify(body)).toBe(expected);
  return body;
}

async function provisionWorld(request: APIRequestContext): Promise<World> {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
  const email = `mes90-dingtalk-${suffix}@example.com`;
  const password = `MES90-${suffix}-Strong!9`;
  const appKey = `dingapp${suffix}`;
  const appSecret = `MES90-${suffix}-DingTalk-Secret!7`;
  const corpId = `dingcorp${suffix}`;
  const conversationId = `cidMES90${suffix}==`;
  const privateConversationId = `cidMES90Private${suffix}==`;
  const senderStaffId = `MES90STAFF${suffix}`;
  const ownerStaffId = `MES90OWNER${suffix}`;

  const registered = await request.post(`${API_BASE}/api/v1/auth/register`, {
    data: { email, password, display_name: 'MES-90 DingTalk Owner' },
  });
  await expectJsonStatus(registered, 201);

  const loggedIn = await request.post(`${API_BASE}/api/v1/auth/login`, {
    data: { email, password },
  });
  const loginBody = (await expectJsonStatus(loggedIn, 200)) as {
    data: { access_token: string };
  };
  const token = loginBody.data.access_token;

  const workspace = await request.post(`${API_BASE}/api/v1/workspaces`, {
    headers: authHeaders(token),
    data: { name: `MES-90 DingTalk ${suffix}`, slug: `mes90-dt-${suffix}` },
  });
  const workspaceBody = (await expectJsonStatus(workspace, 201)) as { data: { id: string } };

  const memberEmail = `mes90-dingtalk-member-${suffix}@example.com`;
  const invited = await request.post(
    `${API_BASE}/api/v1/workspaces/${workspaceBody.data.id}/invitations`,
    {
      headers: authHeaders(token),
      data: { emails: [memberEmail], role: 'member' },
    },
  );
  const invitationBody = (await expectJsonStatus(invited, 201)) as {
    data: Array<{ invite_link: string }>;
  };
  const memberPassword = `MES90-Member-${suffix}-Strong!8`;
  await expectJsonStatus(
    await request.post(`${API_BASE}/api/v1/auth/register`, {
      data: {
        email: memberEmail,
        password: memberPassword,
        display_name: 'MES-90 Project Outsider',
      },
    }),
    201,
  );
  const memberLogin = await request.post(`${API_BASE}/api/v1/auth/login`, {
    data: { email: memberEmail, password: memberPassword },
  });
  const memberLoginBody = (await expectJsonStatus(memberLogin, 200)) as {
    data: { access_token: string };
  };
  const inviteToken = invitationBody.data[0]?.invite_link.split('/').at(-1);
  expect(inviteToken).toBeTruthy();
  await expectJsonStatus(
    await request.post(`${API_BASE}/api/v1/invitations/accept`, {
      headers: authHeaders(memberLoginBody.data.access_token),
      data: { token: inviteToken },
    }),
    200,
  );

  const privateProject = await request.post(
    `${API_BASE}/api/v1/workspaces/${workspaceBody.data.id}/projects`,
    {
      headers: authHeaders(token),
      data: {
        name: `MES-90 Private ${suffix}`,
        key: `DT${suffix.slice(-6).toUpperCase()}`,
        visibility: 'private',
      },
    },
  );
  const privateProjectBody = (await expectJsonStatus(privateProject, 201)) as {
    data: { id: string };
  };

  const agent = await request.post(
    `${API_BASE}/api/v1/workspaces/${workspaceBody.data.id}/agents`,
    {
      headers: authHeaders(token),
      data: {
        name: `MES-90 Responder ${suffix}`,
        role_tag: 'Incident responder',
        system_instructions: 'Handle DingTalk incident requests safely.',
      },
    },
  );
  const agentBody = (await expectJsonStatus(agent, 201)) as { data: { id: string } };

  return {
    token,
    memberToken: memberLoginBody.data.access_token,
    workspaceId: workspaceBody.data.id,
    agentId: agentBody.data.id,
    privateProjectId: privateProjectBody.data.id,
    appKey,
    appSecret,
    corpId,
    conversationId,
    privateConversationId,
    senderStaffId,
    ownerStaffId,
  };
}

function signedHeaders(secret: string): Record<string, string> {
  const timestamp = String(Date.now());
  const signature = createHmac('sha256', secret).update(`${timestamp}\n${secret}`).digest('base64');
  return { timestamp, sign: signature, 'content-type': 'application/json' };
}

async function sendDingTalkCallback(
  request: APIRequestContext,
  world: World,
  text: string,
  sequence: number,
  conversationId = world.conversationId,
): Promise<Record<string, unknown>> {
  const response = await request.post(`${API_BASE}/api/v1/integrations/dingtalk/events`, {
    headers: signedHeaders(world.appSecret),
    data: {
      msgId: `msgMES90${Date.now()}${sequence}==`,
      conversationId,
      conversationType: '2',
      chatbotCorpId: world.corpId,
      robotCode: world.appKey,
      msgtype: 'text',
      senderStaffId: world.senderStaffId,
      senderId: '$:LWCP_v1:$MES90ExternalSender000000000000000',
      senderNick: 'MES-90 On-call',
      isInAtList: true,
      text: { content: ` ${text}` },
    },
  });
  return (await expectJsonStatus(response, 200)) as Record<string, unknown>;
}

async function linkDingTalkIdentity(
  request: APIRequestContext,
  world: World,
  integrationId: string,
  token: string,
  externalUserKey: string,
): Promise<void> {
  const started = await request.post(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/external-identities:link`,
    {
      headers: authHeaders(token),
      data: {
        provider: 'dingtalk',
        integration_id: integrationId,
        external_user_key: externalUserKey,
      },
    },
  );
  await expectJsonStatus(started, 200);

  const outboxKey = `mesh:identity-dev-outbox:dingtalk:${world.corpId}:${externalUserKey}`;
  const redisContainer = process.env.MES90_REDIS_CONTAINER;
  let code: string;
  if (redisContainer !== undefined) {
    const inspected = JSON.parse(
      execFileSync('docker', ['inspect', redisContainer], { encoding: 'utf8' }),
    ) as Array<{ Config: { Cmd: string[] } }>;
    const command = inspected[0]?.Config.Cmd ?? [];
    const passwordIndex = command.indexOf('--requirepass') + 1;
    const redisPassword = passwordIndex > 0 ? command[passwordIndex] : undefined;
    expect(redisPassword, 'Redis container must configure --requirepass').toBeTruthy();
    code = execFileSync(
      'docker',
      [
        'exec',
        '-e',
        `REDISCLI_AUTH=${redisPassword ?? ''}`,
        redisContainer,
        'redis-cli',
        '--raw',
        'GET',
        outboxKey,
      ],
      { encoding: 'utf8' },
    ).trim();
  } else {
    const redisPassword = process.env.MES90_REDIS_PASSWORD;
    expect(
      redisPassword,
      'MES90_REDIS_PASSWORD or MES90_REDIS_CONTAINER is required for identity verification',
    ).toBeTruthy();
    code = execFileSync(
      'redis-cli',
      [
        '-h',
        process.env.MES90_REDIS_HOST ?? '127.0.0.1',
        '-p',
        process.env.MES90_REDIS_PORT ?? '6379',
        '--raw',
        'GET',
        outboxKey,
      ],
      {
        encoding: 'utf8',
        env: { ...process.env, REDISCLI_AUTH: redisPassword },
      },
    ).trim();
  }
  expect(code).toMatch(/^\d{6}$/);

  const confirmed = await request.post(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/external-identities:link-confirm`,
    {
      headers: authHeaders(token),
      data: { provider: 'dingtalk', integration_id: integrationId, code },
    },
  );
  await expectJsonStatus(confirmed, 200);
}

async function activateRuntime(
  request: APIRequestContext,
  world: World,
): Promise<{ runtimeId: string; daemonToken: string }> {
  const created = await request.post(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/runtimes`,
    {
      headers: authHeaders(world.token),
      data: {
        name: 'MES-90 real command runner',
        kind: 'self_hosted',
        labels: {},
        max_concurrent: 1,
      },
    },
  );
  const createdBody = (await expectJsonStatus(created, 201)) as {
    data: { id: string; activation: { code: string } };
  };
  const activated = await request.post(`${API_BASE}/api/v1/daemon/runtimes:activate`, {
    data: { activation_code: createdBody.data.activation.code, metadata: {} },
  });
  const activatedBody = (await expectJsonStatus(activated, 200)) as {
    data: { runtime_token: string };
  };
  return { runtimeId: createdBody.data.id, daemonToken: activatedBody.data.runtime_token };
}

async function claimAndRun(
  request: APIRequestContext,
  runtime: { runtimeId: string; daemonToken: string },
): Promise<ClaimedExecution> {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    const claimed = await request.post(
      `${API_BASE}/api/v1/daemon/runtimes/${runtime.runtimeId}/executions:claim`,
      { headers: authHeaders(runtime.daemonToken), data: { diagnostics: {} } },
    );
    if (claimed.status() === 204) {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
      continue;
    }
    const claimedBody = (await expectJsonStatus(claimed, 200)) as {
      data: {
        execution: { id: string };
        attempt: { id: string; lease_seq: number };
      };
    };
    const running = await request.patch(
      `${API_BASE}/api/v1/daemon/attempts/${claimedBody.data.attempt.id}`,
      {
        headers: authHeaders(runtime.daemonToken),
        data: { lease_seq: claimedBody.data.attempt.lease_seq, status: 'running' },
      },
    );
    await expectJsonStatus(running, 200);
    return {
      executionId: claimedBody.data.execution.id,
      attemptId: claimedBody.data.attempt.id,
      leaseSeq: claimedBody.data.attempt.lease_seq,
    };
  }
  throw new Error('runtime did not claim the DingTalk execution');
}

async function readQueue(
  request: APIRequestContext,
  world: World,
  integrationId: string,
  token = world.token,
) {
  const response = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/queue`,
    { headers: authHeaders(token) },
  );
  const body = (await expectJsonStatus(response, 200)) as { data: QueueItem[] };
  return body.data;
}

async function readQueueSummary(
  request: APIRequestContext,
  world: World,
  integrationId: string,
): Promise<QueueSummary[]> {
  const response = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/queue/summary`,
    { headers: authHeaders(world.token) },
  );
  const body = (await expectJsonStatus(response, 200)) as { data: QueueSummary[] };
  return body.data;
}

async function screenshot(page: Page, name: string): Promise<void> {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(EVIDENCE_DIR, name), fullPage: true });
}

async function screenshotElement(element: Locator, name: string): Promise<void> {
  await element.screenshot({ path: resolve(EVIDENCE_DIR, name) });
}

async function dismissToasts(page: Page): Promise<void> {
  const closeButtons = page.locator('.mesh-toast__close');
  while ((await closeButtons.count()) > 0) {
    await closeButtons.first().click();
  }
}

test('DingTalk dual-mode setup, signed queue lifecycle, commands, and card preview', async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(180_000);
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  const world = await provisionWorld(request);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await injectSession(page, world.token);

  await page.goto('/integrations');
  await expect(page.getByTestId('integrations-page')).toBeVisible({ timeout: 30_000 });

  // Create a Stream integration through the structured UI. The response must
  // acknowledge the top-level secret without reflecting it into config.
  await page.getByTestId('connector-connect-im_dingtalk').click();
  await page.getByTestId('integration-add-name').fill('MES-90 DingTalk Robot');
  await page.getByTestId('integration-dingtalk-app-key').fill(world.appKey);
  await page.getByTestId('integration-dingtalk-corp-id').fill(world.corpId);
  await page.getByTestId('integration-dingtalk-receive-mode').selectOption('stream');
  await page.getByTestId('integration-dingtalk-verbosity').selectOption('progress');
  await page
    .getByTestId('integration-dingtalk-ack-template')
    .fill('MES-90 received; processing now');
  await page.getByTestId('integration-add-secret').fill(world.appSecret);

  const createResponsePromise = page.waitForResponse(
    (response) =>
      response.url() === `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations` &&
      response.request().method() === 'POST',
  );
  await page.getByTestId('integration-add-submit').click();
  const createResponse = await createResponsePromise;
  const createBody = (await createResponse.json()) as IntegrationCreateResponse;
  expect(createResponse.status(), JSON.stringify(createBody)).toBe(201);
  expect(createBody.data.secret_accepted).toBe(true);
  expect(createBody.data.integration.has_secret).toBe(true);
  expect(createBody.data.integration.config).toMatchObject({
    app_key: world.appKey,
    corp_id: world.corpId,
    receive_mode: 'stream',
    inbound_queue: 'serial_conversation',
    verbosity: 'progress',
  });
  expect(JSON.stringify(createBody.data.integration.config)).not.toContain(world.appSecret);
  const integrationId = createBody.data.integration.id;

  await expect(page.getByTestId(`integration-row-${integrationId}`)).toBeVisible({
    timeout: 30_000,
  });
  await page.getByTestId(`integration-detail-${integrationId}`).click();
  await expect(page.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('dingtalk-receive-mode')).toContainText(/Stream/);
  await expect(page.getByTestId('dingtalk-stream-state')).toBeVisible({ timeout: 30_000 });
  await screenshot(page, '01-stream-created.png');

  const diagnosticResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}/stream-status`) &&
      response.request().method() === 'GET',
  );
  await page.getByTestId('dingtalk-diagnose').click();
  const diagnosticResponse = await diagnosticResponsePromise;
  expect([200, 503]).toContain(diagnosticResponse.status());
  if (diagnosticResponse.status() === 503) {
    const diagnosticBody = (await diagnosticResponse.json()) as { error: { code: string } };
    expect(diagnosticBody.error.code).toBe('stream_channel_unavailable');
  }

  // We intentionally have no external enterprise application. Verify that the
  // real outbound adapter reports upstream_error (never the Stream-only 503).
  await page.getByTestId('dingtalk-test-send').click();
  await page.getByTestId('dingtalk-test-conversation-ref').fill(world.conversationId);
  const testSendResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}/test-send`) &&
      response.request().method() === 'POST',
  );
  await page.getByTestId('dingtalk-test-submit').click();
  const testSendResponse = await testSendResponsePromise;
  const testSendBody = (await testSendResponse.json()) as { error: { code: string } };
  expect(testSendResponse.status()).toBe(502);
  expect(testSendBody.error.code).toBe('upstream_error');
  await expect(page.locator('.mesh-toast--danger').last()).toBeVisible();
  await screenshot(page, '02-real-test-send-failure.png');
  await dismissToasts(page);
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /Cancel|取消/ })
    .click();

  // Switch the same real integration to HTTP mode through the edit dialog.
  await page.getByTestId('integration-edit').click();
  await page.getByTestId('integration-edit-dingtalk-receive-mode').selectOption('http');
  const patchResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}`) &&
      response.request().method() === 'PATCH',
  );
  await page.getByTestId('integration-edit-submit').click();
  const patchResponse = await patchResponsePromise;
  const patchBody = (await patchResponse.json()) as {
    data: { config: Readonly<Record<string, unknown>> };
  };
  expect(patchResponse.status(), JSON.stringify(patchBody)).toBe(200);
  expect(patchBody.data.config.receive_mode).toBe('http');
  expect(JSON.stringify(patchBody.data.config)).not.toContain(world.appSecret);
  await expect(page.getByTestId('dingtalk-http-callback')).toContainText(
    '/api/v1/integrations/dingtalk/events',
  );
  await screenshot(page, '03-http-callback-mode.png');

  // Bind the real conversation to the real agent using the DingTalk IM rules.
  await page.getByTestId('integration-tab-bindings').click();
  await page.getByTestId('binding-create').click();
  await page.getByTestId('binding-external-ref').fill(world.conversationId);
  await page.getByTestId('binding-trigger-mention').check();
  await page.getByTestId('binding-trigger-direct_message').check();
  const agentSelect = page.getByTestId('binding-agent-select');
  await expect(agentSelect.locator(`option[value="${world.agentId}"]`)).toHaveCount(1, {
    timeout: 20_000,
  });
  await agentSelect.selectOption(world.agentId);
  const bindResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}/bindings`) &&
      response.request().method() === 'POST',
  );
  await page.getByTestId('binding-submit').click();
  const bindResponse = await bindResponsePromise;
  const bindBody = (await bindResponse.json()) as {
    data: { id: string; provider: string; provider_tenant_key: string; bound_agent_id: string };
  };
  expect(bindResponse.status(), JSON.stringify(bindBody)).toBe(201);
  expect(bindBody.data).toMatchObject({
    provider: 'dingtalk',
    provider_tenant_key: world.corpId,
    bound_agent_id: world.agentId,
  });
  await expect(page.getByTestId(`binding-row-${bindBody.data.id}`)).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId(`binding-agent-${bindBody.data.id}`)).toContainText(
    'MES-90 Responder',
  );

  await page.getByTestId('binding-create').click();
  await page.getByTestId('binding-external-ref').fill(world.privateConversationId);
  await page.getByTestId('binding-scope').selectOption('project');
  await page.getByTestId('binding-project').selectOption(world.privateProjectId);
  await page.getByTestId('binding-trigger-mention').check();
  await page.getByTestId('binding-agent-select').selectOption(world.agentId);
  const privateBindResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}/bindings`) &&
      response.request().method() === 'POST',
  );
  await page.getByTestId('binding-submit').click();
  const privateBindResponse = await privateBindResponsePromise;
  const privateBindBody = (await privateBindResponse.json()) as {
    data: { id: string; scope: string; project_id: string };
  };
  expect(privateBindResponse.status(), JSON.stringify(privateBindBody)).toBe(201);
  expect(privateBindBody.data).toMatchObject({
    scope: 'project',
    project_id: world.privateProjectId,
  });
  await expect(page.getByTestId(`binding-row-${privateBindBody.data.id}`)).toBeVisible();
  await screenshot(page, '04-conversation-bound.png');
  await dismissToasts(page);

  // The ordinary member proves ownership through the complete DingTalk
  // identity triple; a separate owner identity is used by approval callbacks.
  await linkDingTalkIdentity(request, world, integrationId, world.memberToken, world.senderStaffId);
  await linkDingTalkIdentity(request, world, integrationId, world.token, world.ownerStaffId);
  const runtime = await activateRuntime(request, world);

  // /help is consumed by the real command plane and does not create a task.
  const helpResult = await sendDingTalkCallback(request, world, '/help', 0);
  expect(helpResult.process_status).toBe('processed');

  // Burst same-conversation signed messages so serial_conversation yields a
  // stable pending item even while the real worker can dispatch the leader.
  const callbackResults = await Promise.all(
    Array.from({ length: 5 }, (_, index) =>
      sendDingTalkCallback(
        request,
        world,
        `MES-90 queued incident request ${index + 1}`,
        index + 1,
      ),
    ),
  );
  expect(callbackResults.every((result) => result.process_status === 'dispatched')).toBe(true);
  await expect
    .poll(
      () =>
        readImSendPayloads(world.workspaceId).some(
          (payload) =>
            payload.kind === 'ack' &&
            payload.integration_id === integrationId &&
            payload.conversation_key === `dingtalk:${world.corpId}:${world.conversationId}` &&
            payload.template === 'MES-90 received; processing now',
        ),
      { timeout: 15_000, intervals: [100, 250, 500] },
    )
    .toBe(true);

  let pendingItem: QueueItem | undefined;
  await expect
    .poll(
      async () => {
        const items = await readQueue(request, world, integrationId);
        pendingItem = items.find((item) => item.state === 'pending');
        return pendingItem?.id ?? '';
      },
      { timeout: 45_000, intervals: [250, 500, 1000] },
    )
    .not.toBe('');
  expect(pendingItem).toBeDefined();
  const summaries = await readQueueSummary(request, world, integrationId);
  expect(summaries).toContainEqual(
    expect.objectContaining({
      conversation_key: `dingtalk:${world.corpId}:${world.conversationId}`,
      pending_count: expect.any(Number),
    }),
  );
  expect(summaries[0].pending_count).toBeGreaterThan(0);

  const memberContext = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const memberPage = await memberContext.newPage();
  const memberRealtimeFrames: string[] = [];
  memberPage.on('websocket', (socket) => {
    socket.on('framereceived', (event) => {
      memberRealtimeFrames.push(String(event.payload));
    });
  });
  await injectSession(memberPage, world.memberToken);
  await memberPage.goto(`/integrations/${integrationId}`);
  await expect(memberPage.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await memberPage.getByTestId('integration-tab-queue').click();
  await expect(memberPage.getByTestId('integration-queue-panel')).toBeVisible({ timeout: 30_000 });
  await expect(memberPage.getByTestId(`queue-item-${pendingItem!.id}`)).toContainText(
    'MES-90 queued incident request',
  );
  await expect(memberPage.getByTestId(`queue-position-${pendingItem!.id}`)).toBeVisible();
  await expect(memberPage.getByTestId(`queue-cancel-${pendingItem!.id}`)).toBeEnabled();
  await expect(memberPage.getByTestId('queue-audit-open')).toHaveCount(0);
  await screenshotElement(
    memberPage.getByTestId('integration-queue-panel'),
    '05-real-queue-before-cancel.png',
  );

  const cancelResponsePromise = memberPage.waitForResponse(
    (response) =>
      response.url().endsWith(`/queue/${pendingItem!.id}:cancel`) &&
      response.request().method() === 'POST',
  );
  await memberPage.getByTestId(`queue-cancel-${pendingItem!.id}`).click();
  const cancelResponse = await cancelResponsePromise;
  const cancelBody = (await cancelResponse.json()) as { data: { id: string; state: string } };
  expect(cancelResponse.status(), JSON.stringify(cancelBody)).toBe(200);
  expect(cancelBody.data).toEqual({ id: pendingItem!.id, state: 'cancelled' });
  await expect(memberPage.getByTestId(`queue-item-${pendingItem!.id}`)).toContainText(
    /Cancelled|已取消/,
  );
  await expect(memberPage.getByTestId(`queue-cancel-${pendingItem!.id}`)).toBeDisabled();
  await dismissToasts(memberPage);
  await screenshotElement(
    memberPage.getByTestId('integration-queue-panel'),
    '06-real-queue-cancelled.png',
  );

  // Claim the live execution through the real daemon API. Signed /btw writes
  // a context append, while signed /stop drives processing → cancelling →
  // cancelled and cancels the same sender's remaining queued work.
  let processingItem: QueueItem | undefined;
  await expect
    .poll(
      async () => {
        const items = await readQueue(request, world, integrationId);
        processingItem = items.find(
          (item) => item.state === 'processing' && item.execution_id !== null,
        );
        return processingItem?.execution_id ?? '';
      },
      { timeout: 45_000, intervals: [250, 500, 1000] },
    )
    .not.toBe('');
  const firstClaim = await claimAndRun(request, runtime);
  expect(firstClaim.executionId).toBe(processingItem!.execution_id);

  const beforeBtwCount = (await readQueue(request, world, integrationId)).length;
  const btwResult = await sendDingTalkCallback(
    request,
    world,
    '/btw customer impact is rising',
    60,
  );
  expect(btwResult.process_status).toBe('processed');
  const appends = await request.get(
    `${API_BASE}/api/v1/daemon/executions/${firstClaim.executionId}/context-appends`,
    {
      headers: authHeaders(runtime.daemonToken),
      params: { attempt_id: firstClaim.attemptId, since_seq: 0 },
    },
  );
  const appendBody = (await expectJsonStatus(appends, 200)) as {
    data: Array<{ source: string; payload: { text: string } }>;
  };
  expect(appendBody.data).toContainEqual(
    expect.objectContaining({
      source: 'im_btw',
      payload: expect.objectContaining({ text: 'customer impact is rising' }),
    }),
  );
  expect((await readQueue(request, world, integrationId)).length).toBe(beforeBtwCount);

  const stopResult = await sendDingTalkCallback(request, world, '/stop', 61);
  expect(stopResult.process_status).toBe('processed');
  await expect
    .poll(
      () =>
        readImSendPayloads(world.workspaceId).some(
          (payload) =>
            payload.kind === 'command_feedback' &&
            payload.command === 'stop' &&
            payload.stage === 'immediate' &&
            String(payload.text).includes('正在停止任务'),
        ),
      { timeout: 15_000, intervals: [100, 250, 500] },
    )
    .toBe(true);
  await expect
    .poll(
      async () =>
        (await readQueue(request, world, integrationId)).find(
          (item) => item.id === processingItem!.id,
        )?.state,
      { timeout: 30_000, intervals: [200, 500] },
    )
    .toBe('cancelling');
  const afterStop = await readQueue(request, world, integrationId);
  expect(
    afterStop
      .filter((item) => item.id !== processingItem!.id && item.id !== pendingItem!.id)
      .every((item) => item.state === 'cancelled'),
  ).toBe(true);

  const heartbeat = await request.post(
    `${API_BASE}/api/v1/daemon/runtimes/${runtime.runtimeId}:heartbeat`,
    {
      headers: authHeaders(runtime.daemonToken),
      data: {
        current_load: 1,
        health: 'healthy',
        inflight: [firstClaim.attemptId],
        metrics: {},
      },
    },
  );
  const heartbeatBody = (await expectJsonStatus(heartbeat, 200)) as {
    data: { commands: Array<Record<string, unknown>> };
  };
  expect(JSON.stringify(heartbeatBody.data.commands)).toContain(firstClaim.executionId);

  const cancelledAttempt = await request.patch(
    `${API_BASE}/api/v1/daemon/attempts/${firstClaim.attemptId}`,
    {
      headers: authHeaders(runtime.daemonToken),
      data: {
        lease_seq: firstClaim.leaseSeq,
        status: 'cancelled',
        failure_reason: 'cancelled_by_command',
      },
    },
  );
  await expectJsonStatus(cancelledAttempt, 200);
  await expect
    .poll(
      async () =>
        (await readQueue(request, world, integrationId)).find(
          (item) => item.id === processingItem!.id,
        )?.state,
      { timeout: 45_000, intervals: [250, 500, 1000] },
    )
    .toBe('cancelled');
  await expect
    .poll(
      () =>
        readImSendPayloads(world.workspaceId).some(
          (payload) =>
            payload.kind === 'command_feedback' &&
            payload.stage === 'stopped' &&
            payload.queue_item_id === processingItem!.id &&
            String(payload.text).includes('已停止任务'),
        ),
      { timeout: 15_000, intervals: [100, 250, 500] },
    )
    .toBe(true);

  // A second real execution requests approval. The mapped owner clicks the
  // signed DingTalk card callback; success, duplicate and no-permission
  // lifecycle responses all come from the unified approval source of truth.
  const approvalTask = await sendDingTalkCallback(request, world, 'MES-90 approval card task', 70);
  expect(approvalTask.process_status).toBe('dispatched');
  const approvalClaim = await claimAndRun(request, runtime);
  const approvalRequest = await request.post(
    `${API_BASE}/api/v1/daemon/executions/${approvalClaim.executionId}/approvals`,
    {
      headers: authHeaders(runtime.daemonToken),
      data: {
        lease_seq: approvalClaim.leaseSeq,
        attempt_id: approvalClaim.attemptId,
        action_summary: {
          action: 'Deploy MES-90 service',
          capability: 'deploy',
          permission: 'write',
          impact_scope: 'staging service',
        },
        resume_context: { source: 'mes90-real-e2e' },
      },
    },
  );
  const approvalRequestBody = (await expectJsonStatus(approvalRequest, 200)) as {
    data: { id: string; status: string };
  };
  expect(approvalRequestBody.data.status).toBe('pending');
  const approvalId = approvalRequestBody.data.id;
  const approvalCardPayload = {
    outTrackId: `mesh-appr-${approvalId.replaceAll('-', '')}`,
    corpId: world.corpId,
    userId: world.ownerStaffId,
    userIdType: 'staffId',
    content: {
      cardPrivateData: {
        actionIds: ['approve'],
        params: { approval_id: approvalId, decision: 'approve' },
      },
    },
  };
  const cardApproved = await request.post(`${API_BASE}/api/v1/integrations/dingtalk/cards`, {
    headers: signedHeaders(world.appSecret),
    data: approvalCardPayload,
  });
  const cardApprovedBody = (await expectJsonStatus(cardApproved, 200)) as {
    cardData: { cardParamMap: { status_text: string; buttons_disabled: string } };
  };
  expect(cardApprovedBody.cardData.cardParamMap.status_text).toContain('已批准');
  expect(cardApprovedBody.cardData.cardParamMap.buttons_disabled).toBe('true');
  await expectJsonStatus(
    await request.post(`${API_BASE}/api/v1/integrations/dingtalk/cards`, {
      headers: signedHeaders(world.appSecret),
      data: approvalCardPayload,
    }),
    200,
  );
  const forbiddenCard = await request.post(`${API_BASE}/api/v1/integrations/dingtalk/cards`, {
    headers: signedHeaders(world.appSecret),
    data: {
      ...approvalCardPayload,
      userId: 'MES90_UNMAPPED_CLICKER',
      content: {
        cardPrivateData: {
          actionIds: ['reject'],
          params: { approval_id: approvalId, decision: 'reject' },
        },
      },
    },
  });
  const forbiddenCardBody = (await expectJsonStatus(forbiddenCard, 403)) as {
    cardData: { cardParamMap: { status_text: string } };
  };
  expect(forbiddenCardBody.cardData.cardParamMap.status_text).toContain('无权限');

  // A private-project binding produces a real queue row for the owner. The
  // ordinary project outsider receives only a redacted invalidation and sees
  // neither the row via API/UI nor the private binding in the UI.
  const memberFrameStart = memberRealtimeFrames.length;
  const privateResult = await sendDingTalkCallback(
    request,
    world,
    'MES-90 private project incident',
    80,
    world.privateConversationId,
  );
  expect(privateResult.process_status).toBe('dispatched');
  await expect
    .poll(
      async () =>
        (await readQueue(request, world, integrationId)).some((item) =>
          item.message_excerpt.includes('private project incident'),
        ),
      { timeout: 45_000, intervals: [250, 500, 1000] },
    )
    .toBe(true);
  const memberItems = await readQueue(request, world, integrationId, world.memberToken);
  expect(
    memberItems.some((item) => item.message_excerpt.includes('private project incident')),
  ).toBe(false);
  await expect
    .poll(
      () =>
        memberRealtimeFrames.slice(memberFrameStart).find((raw) => {
          try {
            const frame = JSON.parse(raw) as {
              event?: string;
              payload?: Record<string, unknown>;
            };
            return (
              frame.event === 'integration.queue_updated' &&
              frame.payload?.integration_id === integrationId &&
              frame.payload.scope === 'project'
            );
          } catch {
            return false;
          }
        }) ?? '',
      { timeout: 30_000, intervals: [250, 500, 1000] },
    )
    .not.toBe('');
  const privateFrameRaw = memberRealtimeFrames.slice(memberFrameStart).find((raw) => {
    try {
      const frame = JSON.parse(raw) as { event?: string; payload?: Record<string, unknown> };
      return frame.event === 'integration.queue_updated' && frame.payload?.scope === 'project';
    } catch {
      return false;
    }
  });
  const privateFrame = JSON.parse(privateFrameRaw ?? '{}') as {
    payload: Record<string, unknown>;
  };
  expect(privateFrame.payload).not.toHaveProperty('conversation_key');

  await memberPage.goto('/integrations');
  await expect(memberPage.getByTestId(`integration-name-${integrationId}`)).toBeVisible({
    timeout: 30_000,
  });
  await expect(memberPage.getByTestId(`integration-bindings-${integrationId}`)).toHaveText('1');
  await memberPage.goto(`/integrations/${integrationId}`);
  await expect(memberPage.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await memberPage.getByTestId('integration-tab-bindings').click();
  await expect(memberPage.getByTestId(`binding-row-${bindBody.data.id}`)).toBeVisible();
  await expect(memberPage.getByTestId(`binding-row-${privateBindBody.data.id}`)).toHaveCount(0);
  await memberPage.getByTestId('integration-tab-queue').click();
  await expect(memberPage.getByTestId('integration-queue-panel')).toBeVisible();
  await expect(memberPage.getByText('MES-90 private project incident')).toHaveCount(0);
  await screenshotElement(
    memberPage.getByTestId('integration-queue-panel'),
    '08-private-project-outsider-hidden.png',
  );

  await page.getByTestId('integration-tab-queue').click();
  await expect(page.getByTestId('integration-queue-panel')).toBeVisible({ timeout: 30_000 });

  // The Mesh-side guide mirrors the command and card states verified above;
  // it never writes an approval decision locally.
  await page.getByTestId('dingtalk-command-btw').click();
  await expect(page.getByTestId('dingtalk-command-input')).toHaveValue('/btw ');
  await page.getByTestId('dingtalk-command-input').fill('/btw customer impact is rising');
  await expect(page.getByTestId('dingtalk-command-preview')).toContainText('customer impact');
  await page.getByTestId('dingtalk-command-stop').click();
  await expect(page.getByTestId('dingtalk-command-input')).toHaveValue('/stop ');
  await expect(page.getByTestId('dingtalk-stop-feedback')).toContainText(
    /Stopping task|正在停止任务/,
  );
  await page.getByTestId('dingtalk-command-help-button').click();
  await expect(page.getByTestId('dingtalk-command-preview')).toHaveText('/help');

  // Preview terminal card semantics: controls stay disabled and failed/expired
  // states point back to Mesh, which remains the source of truth.
  await expect(page.getByTestId('dingtalk-card-approve')).toBeEnabled();
  await page.getByTestId('dingtalk-card-state').selectOption('expired');
  await expect(page.getByTestId('dingtalk-card-approve')).toBeDisabled();
  await expect(page.getByTestId('dingtalk-card-reject')).toBeDisabled();
  await expect(page.getByTestId('dingtalk-card-fallback')).toBeVisible();
  await expect(page.getByTestId('dingtalk-card-preview')).toContainText(/source|真源|Mesh/);
  await screenshotElement(
    page.locator('.mesh-integrations__interaction-guide'),
    '07-commands-and-card-terminal.png',
  );
  await memberContext.close();
});
