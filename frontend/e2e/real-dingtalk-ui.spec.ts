/**
 * MES-90 DingTalk frontend acceptance against the real Mesh API.
 *
 * Prerequisite: the compose API/worker/gateway stack is exposed through
 * MES90_API_BASE / MES90_WS_BASE. Playwright starts the current Vite frontend
 * through playwright.mes90.config.ts.
 * No Mesh route is mocked: integration creation/editing, binding, signed
 * inbound callbacks, queue reads/cancellation, and outbound diagnostics all hit
 * the real backend and database. A compose-internal OAPI peer proves the exact
 * synthetic app-key/secret relation for first claim and rejects a wrong secret;
 * later sends exercise the real `upstream_error` path. This is controlled local
 * acceptance, not evidence of a live DingTalk test enterprise.
 */
import { Buffer } from 'node:buffer';
import { createHmac } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page, Response as PageResponse } from '@playwright/test';
import { injectSession } from './helpers';

const HERE = dirname(fileURLToPath(import.meta.url));
const API_BASE = process.env.MES90_API_BASE ?? 'http://127.0.0.1:8000';
const WS_BASE = (process.env.MES90_WS_BASE ?? 'ws://127.0.0.1:8081').replace(/\/$/, '');
const EVIDENCE_DIR = process.env.MES90_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'mes90-dingtalk');

interface World {
  readonly token: string;
  readonly memberToken: string;
  readonly workspaceId: string;
  readonly workspaceSlug: string;
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

interface JsonHttpResponse {
  status(): number;
  json(): Promise<unknown>;
}

interface ListedIntegration {
  readonly id: string;
  readonly events_7d: number;
}

interface ListedIntegrationEvent {
  readonly id: string;
  readonly external_event_id: string;
  readonly payload: Readonly<Record<string, unknown>>;
}

type VisualTheme = 'light' | 'dark';

const VISUAL_MATRIX = {
  'desktop-light': { theme: 'light', viewport: { width: 1440, height: 900 } },
  'desktop-dark': { theme: 'dark', viewport: { width: 1440, height: 900 } },
  'phone-light': { theme: 'light', viewport: { width: 390, height: 844 } },
  'phone-dark': { theme: 'dark', viewport: { width: 390, height: 844 } },
} as const satisfies Readonly<
  Record<
    string,
    { readonly theme: VisualTheme; readonly viewport: { width: number; height: number } }
  >
>;

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

function approvalOutTrackId(approvalId: string, integrationId: string): string {
  const uuidToken = (value: string): string =>
    Buffer.from(value.replaceAll('-', ''), 'hex').toString('base64url');
  return `mesh-appr2-${uuidToken(approvalId)}.${uuidToken(integrationId)}`;
}

async function probeRealtimeSubscription(
  page: Page,
  token: string,
  channel: string,
): Promise<Record<string, unknown>> {
  return page.evaluate(
    ({ wsUrl, accessToken, requestedChannel }) =>
      new Promise<Record<string, unknown>>((resolveProbe, rejectProbe) => {
        const socket = new WebSocket(wsUrl);
        const timeout = window.setTimeout(() => {
          socket.close();
          rejectProbe(new Error(`realtime subscription probe timed out: ${requestedChannel}`));
        }, 15_000);
        const finish = (frame: Record<string, unknown>): void => {
          window.clearTimeout(timeout);
          socket.close();
          resolveProbe(frame);
        };
        socket.addEventListener('open', () => {
          socket.send(JSON.stringify({ op: 'auth', token: accessToken }));
        });
        socket.addEventListener('message', (event) => {
          const frame = JSON.parse(String(event.data)) as Record<string, unknown>;
          if (frame.op === 'auth_ok') {
            socket.send(JSON.stringify({ op: 'subscribe', channel: requestedChannel }));
            return;
          }
          if (
            frame.channel === requestedChannel &&
            ['error', 'subscribed'].includes(String(frame.op))
          ) {
            finish(frame);
          }
        });
        socket.addEventListener('error', () => {
          window.clearTimeout(timeout);
          rejectProbe(new Error(`realtime subscription probe failed: ${requestedChannel}`));
        });
      }),
    { wsUrl: `${WS_BASE}/ws`, accessToken: token, requestedChannel: channel },
  );
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

async function expectJsonStatus(response: JsonHttpResponse, expected: number): Promise<unknown> {
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
    workspaceSlug: `mes90-dt-${suffix}`,
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

function persistStreamDownFixture(integrationId: string): void {
  if (!/^[0-9a-f-]{36}$/i.test(integrationId)) throw new Error('invalid integration id');
  const state = runPostgresQuery(
    `UPDATE integrations
       SET stream_state = jsonb_build_object(
         'state', 'down',
         'last_attempt_at', '1970-01-01T00:00:00+00:00',
         'backoff_seconds', 17
       )
     WHERE id = '${integrationId}'
     RETURNING stream_state->>'state'`,
  )
    .trim()
    .split('\n')[0];
  expect(state).toBe('down');
}

async function injectSessionWithTheme(
  page: Page,
  token: string,
  theme: VisualTheme,
): Promise<void> {
  await injectSession(page, token);
  await page.addInitScript((mode: VisualTheme) => {
    window.localStorage.setItem(
      'mesh.settings.v1',
      JSON.stringify({
        state: { preferences: { theme: mode, locale: 'en', timezone: 'UTC' } },
        version: 2,
      }),
    );
  }, theme);
}

function parseRgb(value: string): readonly [number, number, number] {
  const channels =
    value
      .match(/[\d.]+/g)
      ?.slice(0, 3)
      .map(Number) ?? [];
  if (channels.length !== 3 || channels.some((channel) => !Number.isFinite(channel))) {
    throw new Error(`unsupported computed color: ${value}`);
  }
  return channels as unknown as readonly [number, number, number];
}

function relativeLuminance(value: string): number {
  const linear = parseRgb(value).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground: string, background: string): number {
  const light = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const dark = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (light + 0.05) / (dark + 0.05);
}

async function seedVisualQueue(
  request: APIRequestContext,
  world: World,
): Promise<{ integrationId: string; conversationKey: string }> {
  const created = await request.post(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations`,
    {
      headers: authHeaders(world.token),
      data: {
        kind: 'im_dingtalk',
        name: `MES-90 visual ${world.corpId}`,
        config: {
          app_key: world.appKey,
          corp_id: world.corpId,
          robot_code: world.appKey,
          receive_mode: 'http',
          inbound_queue: 'serial_conversation',
          verbosity: 'progress',
          ack_template: 'MES-90 visual received',
        },
        secret: world.appSecret,
      },
    },
  );
  const createdBody = (await expectJsonStatus(created, 201)) as IntegrationCreateResponse;
  const integrationId = createdBody.data.integration.id;

  await expectJsonStatus(
    await request.post(
      `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/bindings`,
      {
        headers: authHeaders(world.token),
        data: {
          external_ref: world.conversationId,
          scope: 'workspace',
          match_config: { trigger_on: ['mention'] },
          bound_agent_id: world.agentId,
        },
      },
    ),
    201,
  );
  const inbound = await sendDingTalkCallback(request, world, 'MES-90 queue contrast evidence', 900);
  expect(inbound.process_status).toBe('dispatched');
  await expect
    .poll(async () => (await readQueue(request, world, integrationId)).length, {
      timeout: 45_000,
      intervals: [100, 250, 500, 1000],
    })
    .toBeGreaterThan(0);
  return {
    integrationId,
    conversationKey: `dingtalk:${world.corpId}:${world.conversationId}`,
  };
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

test('@mes90-functional DingTalk dual-mode setup, signed queue lifecycle, commands, and card preview', async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(180_000);
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  const world = await provisionWorld(request);
  // The compose OAPI peer rejects the wrong secret, and the API must not let
  // that failed proof reserve the app key before the real UI submission.
  const rejectedClaim = await request.post(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations`,
    {
      headers: authHeaders(world.token),
      data: {
        kind: 'im_dingtalk',
        name: 'MES-90 invalid first claim',
        config: {
          app_key: world.appKey,
          corp_id: world.corpId,
          robot_code: world.appKey,
          receive_mode: 'stream',
        },
        secret: `${world.appSecret}-wrong`,
      },
    },
  );
  const rejectedBody = (await expectJsonStatus(rejectedClaim, 422)) as {
    error: { code: string };
  };
  expect(rejectedBody.error.code).toBe('dingtalk_credentials_invalid');
  const afterRejectedClaim = (await expectJsonStatus(
    await request.get(`${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations`, {
      headers: authHeaders(world.token),
    }),
    200,
  )) as { data: unknown[] };
  expect(afterRejectedClaim.data).toHaveLength(0);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await injectSession(page, world.token);
  const ownerRealtimeFrames: string[] = [];
  const ownerRealtimeSentFrames: string[] = [];
  page.on('websocket', (socket) => {
    socket.on('framereceived', (event) => ownerRealtimeFrames.push(String(event.payload)));
    socket.on('framesent', (event) => ownerRealtimeSentFrames.push(String(event.payload)));
  });

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
  // Establish a deterministic persisted failure state so the real UI exposes
  // the explicit reconnect action even when a local Stream gateway happens to
  // accept the synthetic app key. The reconnect itself is exercised solely
  // through the browser and the production API.
  persistStreamDownFixture(integrationId);
  await page.getByTestId(`integration-detail-${integrationId}`).click();
  await expect(page.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('dingtalk-receive-mode')).toContainText(/Stream/);
  await expect(page.getByTestId('dingtalk-stream-state')).toContainText(/Down|已断开/, {
    timeout: 30_000,
  });
  await screenshot(page, '01-stream-created.png');

  const reconnectResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}:reconnect`) &&
      response.request().method() === 'POST',
  );
  const reconnectStatusResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/integrations/${integrationId}/stream-status`) &&
      response.request().method() === 'GET',
  );
  await page.getByTestId('dingtalk-reconnect').click();
  const reconnectResponse = await reconnectResponsePromise;
  const reconnectBody = (await expectJsonStatus(reconnectResponse, 202)) as {
    data: { accepted: boolean };
  };
  expect(reconnectBody.data.accepted).toBe(true);

  const persistedReconnectState = JSON.parse(
    runPostgresQuery(
      `SELECT stream_state::text FROM integrations WHERE id = '${integrationId}'`,
    ).trim(),
  ) as {
    state: string;
    last_attempt_at: string;
    backoff_seconds: number;
    reconnect_request_id: string;
  };
  expect(persistedReconnectState).toMatchObject({
    state: 'reconnecting',
    backoff_seconds: 0,
  });
  expect(persistedReconnectState.last_attempt_at).not.toBe('1970-01-01T00:00:00+00:00');
  expect(persistedReconnectState.reconnect_request_id).toMatch(/^[0-9a-f-]{36}$/i);

  const reconnectStatusResponse = await reconnectStatusResponsePromise;
  const reconnectStatusBody = (await expectJsonStatus(reconnectStatusResponse, 200)) as {
    data: { state: string; last_attempt_at: string; backoff_seconds: number };
  };
  expect(reconnectStatusBody.data).toMatchObject({
    state: 'reconnecting',
    backoff_seconds: 0,
  });
  expect(reconnectStatusBody.data.last_attempt_at).toBe(persistedReconnectState.last_attempt_at);
  await expect(page.getByTestId('dingtalk-stream-state')).toContainText(/Reconnecting|重连中/);

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

  // Load the pending approval from the real source of truth before simulating
  // the external signed card click. The UI must observe the terminal result by
  // polling; this test never clicks its manual refresh action.
  await page.getByTestId('integration-tab-queue').click();
  await expect(page.getByTestId('integration-queue-panel')).toBeVisible({ timeout: 30_000 });
  let approvalReadCount = 0;
  const recordApprovalRead = (response: PageResponse): void => {
    if (
      response.url().endsWith(`/workspaces/${world.workspaceId}/approvals/${approvalId}`) &&
      response.request().method() === 'GET'
    ) {
      approvalReadCount += 1;
    }
  };
  page.on('response', recordApprovalRead);
  await page.getByTestId('dingtalk-approval-id').fill(approvalId);
  const approvalLoadResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/workspaces/${world.workspaceId}/approvals/${approvalId}`) &&
      response.request().method() === 'GET',
  );
  await page.getByTestId('dingtalk-approval-load').click();
  await expectJsonStatus(await approvalLoadResponsePromise, 200);
  const approvalPreview = page.getByTestId('dingtalk-card-preview');
  await expect(approvalPreview).toContainText(approvalId);
  await expect(approvalPreview).toContainText('Deploy MES-90 service');
  await expect(approvalPreview).toContainText(/Pending|待审批/);
  expect(approvalReadCount).toBe(1);

  const approvalCardPayload = {
    outTrackId: approvalOutTrackId(approvalId, integrationId),
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
  await expect(approvalPreview).toContainText(/Approved|已批准/, { timeout: 15_000 });
  expect(approvalReadCount).toBeGreaterThanOrEqual(2);
  await expect(page.getByTestId('dingtalk-approval-refresh')).toBeVisible();
  page.off('response', recordApprovalRead);
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
  // ordinary project outsider receives only a redacted queue invalidation,
  // never a ledger-event frame, and sees neither the row via API/UI nor the
  // private binding in the UI.
  // The owner first opens the real event ledger. Its server-filtered binding
  // list drives a project-channel subscription; the callback below must then
  // add the ledger row without a manual refresh.
  await page.getByTestId('integration-tab-events').click();
  await expect(page.getByTestId('event-ledger')).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(
      () =>
        ownerRealtimeSentFrames.some((raw) => {
          try {
            const frame = JSON.parse(raw) as { op?: string; channel?: string };
            return (
              frame.op === 'subscribe' && frame.channel === `project:${world.privateProjectId}`
            );
          } catch {
            return false;
          }
        }),
      { timeout: 15_000, intervals: [100, 250, 500] },
    )
    .toBe(true);
  const ownerEventRows = page.locator('[data-testid^="event-row-"]');
  const ownerEventCountBefore = await ownerEventRows.count();
  const ownerFrameStart = ownerRealtimeFrames.length;

  // A second, direct socket proves an outsider cannot subscribe to the
  // private project channel even though the user belongs to the workspace.
  const forbiddenProjectSubscription = await probeRealtimeSubscription(
    memberPage,
    world.memberToken,
    `project:${world.privateProjectId}`,
  );
  expect(forbiddenProjectSubscription).toMatchObject({
    op: 'error',
    code: 'forbidden',
    channel: `project:${world.privateProjectId}`,
  });

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
      () =>
        ownerRealtimeFrames.slice(ownerFrameStart).some((raw) => {
          try {
            const frame = JSON.parse(raw) as {
              channel?: string;
              event?: string;
              payload?: Record<string, unknown>;
            };
            return (
              frame.channel === `project:${world.privateProjectId}` &&
              frame.event === 'integration.event_ingested' &&
              frame.payload?.integration_id === integrationId
            );
          } catch {
            return false;
          }
        }),
      { timeout: 30_000, intervals: [100, 250, 500, 1000] },
    )
    .toBe(true);
  await expect(ownerEventRows).toHaveCount(ownerEventCountBefore + 1, { timeout: 30_000 });
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
  await memberPage.waitForTimeout(500);
  const leakedEventFrames = memberRealtimeFrames.slice(memberFrameStart).filter((raw) => {
    try {
      const frame = JSON.parse(raw) as {
        event?: string;
        payload?: Record<string, unknown>;
      };
      return frame.event === 'integration.event_ingested';
    } catch {
      return false;
    }
  });
  expect(leakedEventFrames).toEqual([]);

  // Project visibility must be applied before every read shape: binding rows,
  // event-ledger payloads, and the connected-list 7-day aggregate. Comparing
  // owner/member responses catches both row leaks and aggregate side channels.
  const ownerBindingsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/bindings`,
    { headers: authHeaders(world.token) },
  );
  const ownerBindingsBody = (await expectJsonStatus(ownerBindingsResponse, 200)) as {
    data: Array<{ id: string }>;
  };
  const memberBindingsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/bindings`,
    { headers: authHeaders(world.memberToken) },
  );
  const memberBindingsBody = (await expectJsonStatus(memberBindingsResponse, 200)) as {
    data: Array<{ id: string; external_ref: string; project_id: string | null }>;
  };
  expect(ownerBindingsBody.data.map((binding) => binding.id)).toEqual(
    expect.arrayContaining([bindBody.data.id, privateBindBody.data.id]),
  );
  expect(memberBindingsBody.data).toEqual([
    expect.objectContaining({
      id: bindBody.data.id,
      external_ref: world.conversationId,
      project_id: null,
    }),
  ]);
  expect(JSON.stringify(memberBindingsBody)).not.toContain(world.privateConversationId);
  expect(JSON.stringify(memberBindingsBody)).not.toContain(world.privateProjectId);

  const ownerEventsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/events`,
    { headers: authHeaders(world.token), params: { limit: 200 } },
  );
  const ownerEventsBody = (await expectJsonStatus(ownerEventsResponse, 200)) as {
    data: ListedIntegrationEvent[];
  };
  const memberEventsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations/${integrationId}/events`,
    { headers: authHeaders(world.memberToken), params: { limit: 200 } },
  );
  const memberEventsBody = (await expectJsonStatus(memberEventsResponse, 200)) as {
    data: ListedIntegrationEvent[];
  };
  const privateExternalEventId = String(privateResult.event_id);
  const ownerPrivateEvent = ownerEventsBody.data.find(
    (event) => event.external_event_id === privateExternalEventId,
  );
  expect(ownerPrivateEvent).toBeDefined();
  expect(JSON.stringify(ownerPrivateEvent?.payload)).toContain('MES-90 private project incident');
  expect(JSON.stringify(ownerPrivateEvent?.payload)).toContain(world.privateConversationId);
  expect(
    memberEventsBody.data.some((event) => event.external_event_id === privateExternalEventId),
  ).toBe(false);
  expect(JSON.stringify(memberEventsBody)).not.toContain('MES-90 private project incident');
  expect(JSON.stringify(memberEventsBody)).not.toContain(world.privateConversationId);

  const ownerIntegrationsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations`,
    { headers: authHeaders(world.token), params: { limit: 200 } },
  );
  const ownerIntegrationsBody = (await expectJsonStatus(ownerIntegrationsResponse, 200)) as {
    data: ListedIntegration[];
  };
  const memberIntegrationsResponse = await request.get(
    `${API_BASE}/api/v1/workspaces/${world.workspaceId}/integrations`,
    { headers: authHeaders(world.memberToken), params: { limit: 200 } },
  );
  const memberIntegrationsBody = (await expectJsonStatus(memberIntegrationsResponse, 200)) as {
    data: ListedIntegration[];
  };
  const ownerIntegration = ownerIntegrationsBody.data.find((item) => item.id === integrationId);
  const memberIntegration = memberIntegrationsBody.data.find((item) => item.id === integrationId);
  expect(ownerIntegration).toBeDefined();
  expect(memberIntegration).toBeDefined();
  expect(ownerIntegration!.events_7d).toBe(memberIntegration!.events_7d + 1);
  expect(memberIntegration!.events_7d).toBe(memberEventsBody.data.length);

  await memberPage.goto('/integrations');
  await expect(memberPage.getByTestId(`integration-name-${integrationId}`)).toBeVisible({
    timeout: 30_000,
  });
  await expect(memberPage.getByTestId(`integration-bindings-${integrationId}`)).toHaveText('1');
  await expect(memberPage.getByTestId(`integration-events7d-${integrationId}`)).toHaveText(
    String(memberIntegration!.events_7d),
  );
  await memberPage.goto(`/integrations/${integrationId}`);
  await expect(memberPage.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await memberPage.getByTestId('integration-tab-bindings').click();
  await expect(memberPage.getByTestId(`binding-row-${bindBody.data.id}`)).toBeVisible();
  await expect(memberPage.getByTestId(`binding-row-${privateBindBody.data.id}`)).toHaveCount(0);
  await memberPage.getByTestId('integration-tab-events').click();
  await expect(memberPage.getByTestId('event-ledger')).toBeVisible();
  await expect(memberPage.locator('[data-testid^="event-row-"]')).toHaveCount(
    memberEventsBody.data.length,
  );
  await expect(memberPage.getByText('MES-90 private project incident')).toHaveCount(0);
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

  // The Queue tab was unmounted while the event ledger was open. Reload the
  // exact record and prove the terminal callback truth survives in the server,
  // rather than relying on a tab-local preview. Its fallback is a real deep
  // link to this exact approval, not a generic approval inbox route.
  await page.getByTestId('dingtalk-approval-id').fill(approvalId);
  const approvalReloadResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/workspaces/${world.workspaceId}/approvals/${approvalId}`) &&
      response.request().method() === 'GET',
  );
  await page.getByTestId('dingtalk-approval-load').click();
  await expectJsonStatus(await approvalReloadResponsePromise, 200);
  await expect(page.getByTestId('dingtalk-card-preview')).toContainText(/Approved|已批准/);
  const approvalFallback = page.getByTestId('dingtalk-card-fallback');
  const expectedApprovalPath = `/w/${world.workspaceSlug}/approvals?approval_id=${approvalId}`;
  await expect(approvalFallback).toHaveAttribute('href', expectedApprovalPath);
  await screenshotElement(
    page.locator('.mesh-integrations__interaction-guide'),
    '09-real-approval-auto-refresh.png',
  );
  await memberContext.close();

  await approvalFallback.click();
  await expect(page).toHaveURL(
    new RegExp(`/w/${world.workspaceSlug}/approvals\\?approval_id=${approvalId}$`),
  );
  await expect(page.getByTestId('approvals-focused-detail')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId(`approval-card-${approvalId}`)).toContainText(
    'Deploy MES-90 service',
  );
  await expect(page.getByTestId(`approval-status-${approvalId}`)).toContainText(/Approved|已批准/);
  await screenshot(page, '10-exact-approval-deep-link.png');
});

test('@mes90-visual real queue card keeps WCAG AA contrast across the four UI combinations', async ({
  page,
  request,
}, testInfo) => {
  const projectName = testInfo.project.name as keyof typeof VISUAL_MATRIX;
  const variant = VISUAL_MATRIX[projectName] as
    (typeof VISUAL_MATRIX)[keyof typeof VISUAL_MATRIX] | undefined;
  test.skip(variant === undefined, 'run with playwright.mes90.config.ts');
  if (variant === undefined) return;

  test.setTimeout(120_000);
  expect(page.viewportSize()).toEqual(variant.viewport);
  const world = await provisionWorld(request);
  await expectJsonStatus(
    await request.patch(`${API_BASE}/api/v1/users/me`, {
      headers: authHeaders(world.token),
      data: { settings: { theme: variant.theme } },
    }),
    200,
  );
  const visualWorld = await seedVisualQueue(request, world);
  await injectSessionWithTheme(page, world.token, variant.theme);
  await page.goto(`/integrations/${visualWorld.integrationId}`);
  await expect(page.getByTestId('integration-detail')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('html')).toHaveAttribute('data-theme', variant.theme);
  await page.getByTestId('integration-tab-queue').click();
  await expect(page.getByTestId('integration-queue-panel')).toBeVisible({ timeout: 30_000 });

  const queueCard = page.getByTestId(`queue-conversation-${visualWorld.conversationKey}`);
  await expect(queueCard).toContainText('MES-90 queue contrast evidence', { timeout: 30_000 });
  const colors = await queueCard.evaluate((element) => {
    const cardStyle = window.getComputedStyle(element);
    const textElement = element.querySelector('.mesh-integrations__queue-excerpt');
    const metaElement = element.querySelector('.mesh-integrations__queue-meta');
    const mutedElement = element.querySelector('.mesh-integrations__muted');
    if (textElement === null || metaElement === null || mutedElement === null) {
      throw new Error('queue card text fixtures are incomplete');
    }
    return {
      background: cardStyle.backgroundColor,
      inherited: cardStyle.color,
      excerpt: window.getComputedStyle(textElement).color,
      meta: window.getComputedStyle(metaElement).color,
      muted: window.getComputedStyle(mutedElement).color,
    };
  });
  expect(colors.background).not.toBe('rgba(0, 0, 0, 0)');
  const contrastEvidence: Record<string, number> = {};
  for (const [label, foreground] of Object.entries(colors).filter(
    ([label]) => label !== 'background',
  )) {
    const ratio = contrastRatio(foreground, colors.background);
    contrastEvidence[label] = Number(ratio.toFixed(3));
    expect(ratio, `${projectName} ${label} contrast ${ratio.toFixed(3)}`).toBeGreaterThanOrEqual(
      4.5,
    );
  }
  console.log(
    `MES90_CONTRAST ${JSON.stringify({
      project: projectName,
      theme: variant.theme,
      viewport: variant.viewport,
      ratios: contrastEvidence,
    })}`,
  );

  const evidenceDirectory = resolve(EVIDENCE_DIR, 'matrix', projectName);
  mkdirSync(evidenceDirectory, { recursive: true });
  await queueCard.scrollIntoViewIfNeeded();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: resolve(evidenceDirectory, 'queue-viewport.png'),
    fullPage: false,
  });
});
