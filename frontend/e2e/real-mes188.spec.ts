/**
 * MES-188 production-shaped acceptance.
 *
 * No route interception or fixture server is used. Every request crosses the
 * loopback nginx front door into FastAPI, PostgreSQL, Redis, MinIO, the worker
 * and realtime gateway. The desktop-light journey additionally interrupts the
 * real API container to prove optimistic sending/failure/retry with one stable
 * idempotency key, then drives agent/runtime lifecycle and attempt approval
 * protocols over their public HTTP surfaces.
 */
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, APIResponse, Page, TestInfo } from '@playwright/test';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = resolve(HERE, '../../docs/evidence/mes-188');
const API_CONTAINER = process.env.MES188_API_CONTAINER ?? 'mes188-real-api-1';
const PG_CONTAINER = process.env.MES188_PG_CONTAINER ?? 'mes188-real-postgres-1';
const RESPONSE_TIMEOUT = 30_000;
const WORKER_TIMEOUT = 60_000;
const REDACTION_SECRET = 'MES188-credential-must-never-render';

interface Envelope<T> {
  readonly data: T;
}

interface Workspace {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
}

interface UserProfile {
  readonly user: { readonly id: string };
}

interface Agent {
  readonly id: string;
  readonly name: string;
  readonly lifecycle_status: string;
  readonly active_config_version_id: string;
  readonly member: { readonly id: string };
  readonly capacity: {
    readonly running: number;
    readonly queued: number;
    readonly awaiting_approval: number;
  };
}

interface Issue {
  readonly id: string;
  readonly identifier: string;
  readonly state_category: string;
}

interface CommentRecord {
  readonly id: string;
}

interface RuntimeRecord {
  readonly id: string;
  readonly operational_state?: string;
}

interface ActivatedRuntime {
  readonly id: string;
  readonly token: string;
}

interface Attempt {
  readonly id: string;
  readonly attempt_number: number;
  readonly task_token?: string;
}

interface Execution {
  readonly id: string;
  readonly status: string;
  readonly issue_id: string | null;
  readonly failure_reason: string | null;
  readonly attempts: readonly Attempt[];
  readonly approval_audits?: readonly { readonly id: string }[];
}

interface Approval {
  readonly id: string;
  readonly status: string;
}

interface Notification {
  readonly id: string;
  readonly type: string;
  readonly priority: string;
  readonly execution_id: string | null;
  readonly preview: string;
}

interface World {
  readonly ownerEmail: string;
  readonly ownerPassword: string;
  readonly ownerToken: string;
  readonly peerEmail: string;
  readonly peerPassword: string;
  readonly peerToken: string;
  readonly peerUserId: string;
  readonly workspace: Workspace;
  readonly agent: Agent;
  readonly firstConfigVersionId: string;
  readonly secondConfigVersionId: string;
  readonly issue: Issue;
  readonly lifecycleIssue: Issue;
  readonly rootComment: CommentRecord;
  readonly reply: CommentRecord;
  readonly tombstone: CommentRecord;
  readonly onlineRuntime: ActivatedRuntime;
  readonly degradedRuntime: ActivatedRuntime;
  readonly isolatedRuntime: ActivatedRuntime;
  readonly pausedRuntime: ActivatedRuntime;
  readonly primaryExecution: Execution;
  readonly primaryAttempt: Attempt;
  readonly approval: Approval;
}

async function dataOf<T>(response: { json(): Promise<unknown> }): Promise<T> {
  return ((await response.json()) as Envelope<T>).data;
}

async function postData<T>(
  request: APIRequestContext,
  path: string,
  token: string | null,
  data: Record<string, unknown>,
  expectedStatus = 201,
): Promise<T> {
  const response = await request.post(path, {
    headers: token === null ? undefined : { Authorization: `Bearer ${token}` },
    data,
  });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(expectedStatus);
  return dataOf<T>(response);
}

async function patchData<T>(
  request: APIRequestContext,
  path: string,
  token: string,
  data: Record<string, unknown>,
): Promise<T> {
  const response = await request.patch(path, {
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(200);
  return dataOf<T>(response);
}

async function getData<T>(request: APIRequestContext, path: string, token: string): Promise<T> {
  const response = await request.get(path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(200);
  return dataOf<T>(response);
}

async function registerAndLogin(
  request: APIRequestContext,
  email: string,
  password: string,
  displayName: string,
): Promise<{ readonly token: string; readonly userId: string }> {
  const registration = await request.post('/api/v1/auth/register', {
    data: { email, password, display_name: displayName },
  });
  expect([200, 201], await registration.text()).toContain(registration.status());
  const login = await request.post('/api/v1/auth/login', { data: { email, password } });
  expect(login.status(), await login.text()).toBe(200);
  const token = (await dataOf<{ readonly access_token: string }>(login)).access_token;
  const profile = await getData<UserProfile>(request, '/api/v1/users/me', token);
  return { token, userId: profile.user.id };
}

async function createAndActivateRuntime(
  request: APIRequestContext,
  workspaceId: string,
  ownerToken: string,
  name: string,
  report:
    | { readonly state: 'online' }
    | {
        readonly state: 'degraded' | 'isolated';
        readonly reason: 'provider_unavailable' | 'security_anomaly';
      },
): Promise<ActivatedRuntime> {
  const response = await request.post(`/api/v1/workspaces/${workspaceId}/runtimes`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { name, kind: 'self_hosted', labels: { suite: 'mes188' }, max_concurrent: 2 },
  });
  expect(response.status(), await response.text()).toBe(201);
  const created = await dataOf<RuntimeRecord & { readonly activation: { readonly code: string } }>(
    response,
  );
  const activated = await postData<{ readonly runtime_token: string }>(
    request,
    '/api/v1/daemon/runtimes:activate',
    null,
    {
      activation_code: created.activation.code,
      metadata: {
        hostname: `${name}.mesh.test`,
        os: 'linux-x86_64',
        cpu_cores: 8,
        memory_mb: 16384,
        capabilities: ['python', 'version_control'],
        version: '0.3.0-mes188',
      },
      protocol_version: 1,
      provider_manifest: {
        provider: 'claude-code',
        version: '2.1.218',
        model: 'mes188-e2e-model',
      },
      daemon_features: { sandbox: 'linux_ns', broker: 'unix', egress: 'gateway' },
    },
    200,
  );
  const diagnostics =
    report.state === 'online'
      ? []
      : [
          {
            reason_code: report.reason,
            missing_capabilities: report.reason === 'provider_unavailable' ? ['claude-code'] : [],
            affected_task_types: ['agent-task'],
          },
        ];
  await postData(
    request,
    `/api/v1/daemon/runtimes/${created.id}:heartbeat`,
    activated.runtime_token,
    {
      current_load: 0,
      health: report.state === 'online' ? 'healthy' : 'degraded',
      operational_state: report.state,
      diagnostics,
      metrics: { source: 'mes188-real' },
      protocol_version: 1,
    },
    200,
  );
  return { id: created.id, token: activated.runtime_token };
}

async function listIssueExecutions(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  issueId: string,
): Promise<readonly Execution[]> {
  return getData<readonly Execution[]>(
    request,
    `/api/v1/workspaces/${world.workspace.id}/executions?issue_id=${issueId}&limit=100`,
    world.ownerToken,
  );
}

async function waitForExecution(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  issueId: string,
  excludedIds: ReadonlySet<string> = new Set(),
): Promise<Execution> {
  let found: Execution | undefined;
  await expect
    .poll(
      async () => {
        const executions = await listIssueExecutions(request, world, issueId);
        found = executions.find((execution) => !excludedIds.has(execution.id));
        return found?.id ?? null;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .not.toBeNull();
  return found as Execution;
}

async function getExecution(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  executionId: string,
): Promise<Execution> {
  return getData<Execution>(
    request,
    `/api/v1/workspaces/${world.workspace.id}/executions/${executionId}`,
    world.ownerToken,
  );
}

async function waitForExecutionStatus(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  executionId: string,
  expected: string,
): Promise<Execution> {
  let detail: Execution | undefined;
  await expect
    .poll(
      async () => {
        detail = await getExecution(request, world, executionId);
        return detail.status;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .toBe(expected);
  return detail as Execution;
}

async function claim(
  request: APIRequestContext,
  runtime: ActivatedRuntime,
): Promise<{ readonly execution: Execution; readonly attempt: Attempt }> {
  let response: APIResponse | null = null;
  await expect
    .poll(
      async () => {
        response = await request.post(`/api/v1/daemon/runtimes/${runtime.id}/executions:claim`, {
          headers: { Authorization: `Bearer ${runtime.token}` },
          data: { diagnostics: {} },
        });
        return response.status();
      },
      { timeout: WORKER_TIMEOUT },
    )
    .toBe(200);
  const claimedResponse: APIResponse | null = response;
  if (claimedResponse === null) throw new Error('claim did not produce a response');
  return dataOf<{ readonly execution: Execution; readonly attempt: Attempt }>(claimedResponse);
}

async function transitionAttempt(
  request: APIRequestContext,
  runtime: ActivatedRuntime,
  attemptId: string,
  status: 'running' | 'completed' | 'failed',
  extra: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return patchData<Record<string, unknown>>(
    request,
    `/api/v1/daemon/attempts/${attemptId}`,
    runtime.token,
    { lease_seq: 1, status, ...extra },
  );
}

async function appendLogs(
  request: APIRequestContext,
  runtime: ActivatedRuntime,
  attemptId: string,
  lines: readonly string[],
): Promise<void> {
  await postData(
    request,
    `/api/v1/daemon/attempts/${attemptId}/logs`,
    runtime.token,
    { lease_seq: 1, stream: 'stdout', start_offset: 0, lines, sealed: true },
    200,
  );
}

function validResult(label: string): Record<string, unknown> {
  return {
    schema_version: 1,
    provider: {
      name: 'claude-code',
      version: '2.1.218',
      model: 'mes188-e2e-model',
      session_id: `mes188-${label}`,
    },
    usage: {
      turns: 2,
      cost_usd: '0.021000',
      input_tokens: 120,
      output_tokens: 40,
      total_tokens: 172,
      cache_read_tokens: 5,
      cache_creation_tokens: 7,
    },
    outcome: { summary: `MES-188 ${label}`, exit_code: 0, termination: 'completed' },
    artifacts: { diff_ref: null, checkout_id: null },
    redaction: { hit_count: 1, rule_version: 'redaction-v1' },
  };
}

async function seedWorld(request: APIRequestContext, label: string): Promise<World> {
  const suffix = `${label}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
    .replaceAll('_', '-')
    .toLowerCase();
  const ownerEmail = `mes188-owner-${suffix}@example.com`;
  const peerEmail = `mes188-peer-${suffix}@example.com`;
  const ownerPassword = `Mesh#Owner-${suffix}A9!`;
  const peerPassword = `Mesh#Peer-${suffix}B8!`;
  const owner = await registerAndLogin(request, ownerEmail, ownerPassword, 'MES-188 Owner');
  const peer = await registerAndLogin(request, peerEmail, peerPassword, 'MES-188 Peer');
  const workspace = await postData<Workspace>(request, '/api/v1/workspaces', owner.token, {
    name: `MES-188 ${label}`,
    slug: `m188-${suffix}`.slice(0, 32),
  });
  await postData(request, `/api/v1/workspaces/${workspace.id}/views`, owner.token, {
    name: 'MES-188 Board',
    layout: 'board',
    visibility: 'shared',
    group_by: 'state_category',
    is_default: true,
  });
  const invitation = await postData<readonly { readonly invite_link: string }[]>(
    request,
    `/api/v1/workspaces/${workspace.id}/invitations`,
    owner.token,
    { emails: [peerEmail], role: 'member' },
  );
  const invitationToken = invitation[0]?.invite_link.split('/').pop();
  expect(invitationToken).toBeTruthy();
  await postData(
    request,
    '/api/v1/invitations/accept',
    peer.token,
    { token: invitationToken },
    200,
  );

  const agent = await postData<Agent>(
    request,
    `/api/v1/workspaces/${workspace.id}/agents`,
    owner.token,
    {
      name: `MES-188 Agent ${label}`,
      role_tag: 'Runtime operator',
      system_instructions: 'Return concise test evidence.',
      trigger_on_assign: true,
      model_config: {
        provider: 'claude-code',
        model: 'mes188-e2e-model',
        model_tier: 'balanced',
        temperature: 0.2,
        max_tokens: 4096,
        budget: { max_cost_usd: '0.50', max_turns: 3, max_tokens: 4096 },
      },
    },
  );
  const firstConfigVersionId = agent.active_config_version_id;
  const updatedAgent = await patchData<Agent>(
    request,
    `/api/v1/workspaces/${workspace.id}/agents/${agent.id}/config`,
    owner.token,
    {
      model_config: { temperature: 0.4 },
      system_instructions: 'Return concise, audited test evidence.',
      change_summary: 'MES-188 audit configuration',
    },
  );

  await postData(request, `/api/v1/workspaces/${workspace.id}/credentials`, owner.token, {
    name: 'MES-188 redaction probe',
    kind: 'env',
    scope: 'execution',
    value: REDACTION_SECRET,
    env_name: 'MES188_REDACTION_PROBE',
    redact_in_logs: true,
  });

  const onlineRuntime = await createAndActivateRuntime(
    request,
    workspace.id,
    owner.token,
    `mes188-online-${suffix}`,
    { state: 'online' },
  );
  const degradedRuntime = await createAndActivateRuntime(
    request,
    workspace.id,
    owner.token,
    `mes188-degraded-${suffix}`,
    { state: 'degraded', reason: 'provider_unavailable' },
  );
  const isolatedRuntime = await createAndActivateRuntime(
    request,
    workspace.id,
    owner.token,
    `mes188-isolated-${suffix}`,
    { state: 'isolated', reason: 'security_anomaly' },
  );
  const pausedRuntime = await createAndActivateRuntime(
    request,
    workspace.id,
    owner.token,
    `mes188-paused-${suffix}`,
    { state: 'online' },
  );
  await postData(
    request,
    `/api/v1/workspaces/${workspace.id}/runtimes/${pausedRuntime.id}:pause`,
    owner.token,
    {},
    200,
  );

  const issue = await postData<Issue>(
    request,
    `/api/v1/workspaces/${workspace.id}/issues`,
    owner.token,
    {
      title: `MES-188 observable run ${label}`,
      description: 'Real assignment, approval, logs and review lifecycle.',
      assignee_id: agent.member.id,
    },
  );
  const lifecycleIssue = await postData<Issue>(
    request,
    `/api/v1/workspaces/${workspace.id}/issues`,
    owner.token,
    { title: `MES-188 lifecycle probes ${label}` },
  );
  const rootComment = await postData<CommentRecord>(
    request,
    `/api/v1/issues/${issue.id}/comments`,
    owner.token,
    { body_markdown: 'Resolved thread root from the real API.' },
  );
  const reply = await postData<CommentRecord>(
    request,
    `/api/v1/issues/${issue.id}/comments`,
    owner.token,
    { body_markdown: 'A persisted single-level reply.', parent_id: rootComment.id },
  );
  await postData(request, `/api/v1/comments/${rootComment.id}/resolve`, owner.token, {}, 200);
  const tombstone = await postData<CommentRecord>(
    request,
    `/api/v1/issues/${issue.id}/comments`,
    owner.token,
    { body_markdown: 'This body is replaced by a deletion tombstone.' },
  );
  const deleted = await request.delete(`/api/v1/comments/${tombstone.id}`, {
    headers: { Authorization: `Bearer ${owner.token}` },
  });
  expect(deleted.status(), await deleted.text()).toBe(204);

  const baseWorld = { workspace, ownerToken: owner.token };
  const primaryExecution = await waitForExecution(request, baseWorld, issue.id);
  expect(primaryExecution.status).toBe('queued');
  const claimed = await claim(request, onlineRuntime);
  expect(claimed.execution.id).toBe(primaryExecution.id);
  expect(claimed.attempt.task_token).toBeTruthy();
  await transitionAttempt(request, onlineRuntime, claimed.attempt.id, 'running');
  await appendLogs(request, onlineRuntime, claimed.attempt.id, [
    'MES-188 provider started',
    `credential=${REDACTION_SECRET}`,
  ]);
  const agentComment = await postData<CommentRecord>(
    request,
    `/api/v1/task/issues/${issue.id}/comments`,
    claimed.attempt.task_token as string,
    { body: 'Agent progress arrived through the scoped task token.' },
  );
  expect(agentComment.id).toBeTruthy();
  const approval = await postData<Approval>(
    request,
    `/api/v1/daemon/executions/${primaryExecution.id}/approvals`,
    onlineRuntime.token,
    {
      lease_seq: 1,
      attempt_id: claimed.attempt.id,
      action_summary: { action: 'exec:shell', command_class: 'write', target: 'workspace' },
      resume_context: { checkpoint_ref: 'mes188-checkpoint-1' },
    },
    200,
  );
  await waitForExecutionStatus(request, baseWorld, primaryExecution.id, 'awaiting_approval');
  const capacity = await getData<Agent>(
    request,
    `/api/v1/workspaces/${workspace.id}/agents/${agent.id}`,
    owner.token,
  );
  expect(capacity.capacity).toEqual({ running: 0, queued: 0, awaiting_approval: 1 });

  return {
    ownerEmail,
    ownerPassword,
    ownerToken: owner.token,
    peerEmail,
    peerPassword,
    peerToken: peer.token,
    peerUserId: peer.userId,
    workspace,
    agent: updatedAgent,
    firstConfigVersionId,
    secondConfigVersionId: updatedAgent.active_config_version_id,
    issue,
    lifecycleIssue,
    rootComment,
    reply,
    tombstone,
    onlineRuntime,
    degradedRuntime,
    isolatedRuntime,
    pausedRuntime,
    primaryExecution,
    primaryAttempt: claimed.attempt,
    approval,
  };
}

async function loginThroughUi(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(password);
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === 'POST' &&
      new URL(candidate.url()).pathname === '/api/v1/auth/login',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('login-account-submit').click();
  expect((await response).status()).toBe(200);
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
}

async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  if (theme === 'dark') {
    await page.goto('/settings/appearance');
    const response = page.waitForResponse(
      (candidate) =>
        candidate.request().method() === 'PATCH' &&
        new URL(candidate.url()).pathname === '/api/v1/users/me',
      { timeout: RESPONSE_TIMEOUT },
    );
    await page.getByTestId('theme-select').selectOption('dark');
    expect((await response).status()).toBe(200);
  }
  try {
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme, {
      timeout: 10_000,
    });
  } catch {
    // Under load the client-side apply can race the persisted preference;
    // the setting is server-side, so a reload applies it deterministically.
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  }
}

async function capture(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({
    path: join(EVIDENCE_DIR, `${testInfo.project.name}-${name}.png`),
    fullPage: true,
  });
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

/**
 * Wait for a board card to materialize on any viewport. Desktop renders every
 * column at once, so the card appears in place once assignment materializes.
 * Mobile collapses columns into a lane-tab strip starting on the first column,
 * so the card may sit on a later tab: keep sweeping the tabs (like a phone
 * user flicking through lanes) until the card shows or the deadline passes.
 * The board skeleton needs a moment to render either way, so the loop also
 * covers the "tablist not present yet" window instead of bailing out early.
 */
async function focusBoardLane(page: Page, issueId: string): Promise<void> {
  const card = page.getByTestId(`board-card-${issueId}`);
  const tabs = page.getByRole('tablist', { name: 'Board columns' }).getByRole('tab');
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await card.isVisible()) {
      return;
    }
    const tabCount = await tabs.count();
    for (let index = 0; index < tabCount; index += 1) {
      await tabs.nth(index).click();
      if (await card.isVisible()) {
        return;
      }
    }
    await page.waitForTimeout(500);
  }
  if (await card.isVisible()) {
    return;
  }
  throw new Error(`board card ${issueId} not found on any viewport/lane tab`);
}

async function expectForbiddenExecutionSubscription(
  page: Page,
  token: string,
  channel: string,
): Promise<void> {
  const frame = await page.evaluate(
    async ({ authToken, targetChannel, timeoutMs }) =>
      new Promise<{ readonly op?: string; readonly code?: string; readonly channel?: string }>(
        (resolveFrame, rejectFrame) => {
          const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
          const socket = new WebSocket(`${scheme}//${window.location.host}/ws`);
          const timeout = window.setTimeout(() => {
            socket.close();
            rejectFrame(new Error(`timed out waiting for ${targetChannel} authorization`));
          }, timeoutMs);
          socket.addEventListener('open', () => {
            socket.send(JSON.stringify({ op: 'auth', token: authToken }));
          });
          socket.addEventListener('message', (event) => {
            const payload = JSON.parse(String(event.data)) as {
              readonly op?: string;
              readonly code?: string;
              readonly channel?: string;
            };
            if (payload.op === 'auth_ok') {
              socket.send(JSON.stringify({ op: 'subscribe', channel: targetChannel }));
              return;
            }
            if (payload.op === 'error' && payload.channel === targetChannel) {
              window.clearTimeout(timeout);
              socket.close();
              resolveFrame(payload);
            }
          });
          socket.addEventListener('error', () => {
            window.clearTimeout(timeout);
            rejectFrame(new Error('realtime socket failed before authorization response'));
          });
        },
      ),
    { authToken: token, targetChannel: channel, timeoutMs: RESPONSE_TIMEOUT },
  );
  expect(frame).toMatchObject({ op: 'error', code: 'forbidden', channel });
}

async function clickCommentAction(
  page: Page,
  commentId: string,
  action: 'reopen' | 'resolve',
): Promise<void> {
  await page.getByTestId(`comment-card-${commentId}`).hover();
  const button = page.getByTestId(`comment-${action}-${commentId}`);
  await expect(button).toBeVisible();
  await button.click();
}

async function verifyTwoAccountRealtimeAndPrivacy(
  ownerPage: Page,
  peerPage: Page,
  request: APIRequestContext,
  world: World,
): Promise<void> {
  const issuePath = `/w/${world.workspace.slug}/issues/${world.issue.identifier}`;
  await peerPage.goto(issuePath);
  await expect(peerPage.getByTestId('resolved-threads-toggle')).toHaveAttribute(
    'aria-expanded',
    'false',
  );

  // Both independent browser contexts must converge after each persisted
  // thread transition, without a refresh or a duplicated reply.
  await clickCommentAction(ownerPage, world.rootComment.id, 'reopen');
  await expect(peerPage.getByTestId(`comment-card-${world.rootComment.id}`)).toBeVisible();
  await clickCommentAction(ownerPage, world.rootComment.id, 'resolve');
  await expect(peerPage.getByTestId(`comment-card-${world.rootComment.id}`)).toHaveCount(0);
  await expect(peerPage.getByTestId('resolved-threads-toggle')).toBeVisible();

  const privateProject = await postData<{ readonly id: string }>(
    request,
    `/api/v1/workspaces/${world.workspace.id}/projects`,
    world.ownerToken,
    {
      name: `MES-188 Private ${Date.now().toString(36)}`,
      key: `P${Math.random().toString(36).slice(2, 7).toUpperCase()}`,
      visibility: 'private',
    },
  );
  const privateIssue = await postData<Issue>(
    request,
    `/api/v1/workspaces/${world.workspace.id}/issues`,
    world.ownerToken,
    {
      title: 'MES-188 private execution boundary',
      project_id: privateProject.id,
      assignee_id: world.agent.member.id,
    },
  );
  const privateExecution = await waitForExecution(request, world, privateIssue.id);

  for (const path of [
    `/api/v1/workspaces/${world.workspace.id}/executions/${privateExecution.id}`,
    `/api/v1/workspaces/${world.workspace.id}/executions/${privateExecution.id}/logs`,
  ]) {
    const response = await request.get(path, {
      headers: { Authorization: `Bearer ${world.peerToken}` },
    });
    expect(response.status(), `${path}: ${await response.text()}`).toBe(403);
  }
  const cancelResponse = await request.post(
    `/api/v1/workspaces/${world.workspace.id}/executions/${privateExecution.id}:cancel`,
    { headers: { Authorization: `Bearer ${world.peerToken}` }, data: {} },
  );
  expect(cancelResponse.status(), await cancelResponse.text()).toBe(403);
  await expectForbiddenExecutionSubscription(
    peerPage,
    world.peerToken,
    `execution:${privateExecution.id}`,
  );
  await expectForbiddenExecutionSubscription(
    peerPage,
    world.peerToken,
    `execution:${privateExecution.id}:logs`,
  );

  // Keep later claims deterministic: the owner may stop the private queued
  // execution, while the outsider remains unable to observe or mutate it.
  await postData(
    request,
    `/api/v1/workspaces/${world.workspace.id}/executions/${privateExecution.id}:cancel`,
    world.ownerToken,
    {},
    200,
  );
}

async function triggerExecution(
  request: APIRequestContext,
  world: World,
  marker: string,
  issueId: string = world.lifecycleIssue.id,
): Promise<Execution> {
  const existing = new Set(
    (await listIssueExecutions(request, world, issueId)).map((item) => item.id),
  );
  await postData<CommentRecord>(request, `/api/v1/issues/${issueId}/comments`, world.ownerToken, {
    body_markdown: `[@${world.agent.name}](mention://member/${world.agent.member.id}) ${marker}`,
  });
  return waitForExecution(request, world, issueId, existing);
}

async function lifecycleStatus(
  request: APIRequestContext,
  world: World,
  expected: string,
): Promise<void> {
  await expect
    .poll(
      async () =>
        (
          await getData<Agent>(
            request,
            `/api/v1/workspaces/${world.workspace.id}/agents/${world.agent.id}`,
            world.ownerToken,
          )
        ).lifecycle_status,
    )
    .toBe(expected);
}

async function runOptimisticRetry(
  page: Page,
  request: APIRequestContext,
  world: World,
): Promise<void> {
  const composer = page.getByTestId('composer-input').last();
  await expect(composer).toBeVisible();

  let containerStarted = true;
  let containerPaused = false;
  execFileSync('docker', ['pause', API_CONTAINER], { timeout: 30_000 });
  containerPaused = true;
  try {
    await composer.fill('MES-188 retry keeps one idempotent comment.');
    await page.getByTestId('composer-submit').last().click();
    await expect(page.getByTestId('comment-delivery-sending')).toBeVisible();
    execFileSync('docker', ['kill', API_CONTAINER], { timeout: 30_000 });
    containerStarted = false;
    containerPaused = false;
    await expect(page.getByTestId('comment-delivery-failed')).toBeVisible({ timeout: 30_000 });
    execFileSync('docker', ['start', API_CONTAINER], { timeout: 30_000 });
    containerStarted = true;
    await expect
      .poll(async () => (await request.get('/readyz')).status(), { timeout: 90_000 })
      .toBe(200);
    await page.getByTestId('comment-delivery-retry').click();
    await expect(page.getByTestId('comment-delivery-failed')).toHaveCount(0);
    await expect
      .poll(async () => {
        const comments = await getData<readonly { readonly body_markdown: string }[]>(
          request,
          `/api/v1/issues/${world.issue.id}/comments?include=replies&limit=100`,
          world.ownerToken,
        );
        return comments.filter(
          (comment) => comment.body_markdown === 'MES-188 retry keeps one idempotent comment.',
        ).length;
      })
      .toBe(1);
  } finally {
    if (!containerStarted) {
      execFileSync('docker', ['start', API_CONTAINER], { timeout: 30_000 });
    } else if (containerPaused) {
      execFileSync('docker', ['unpause', API_CONTAINER], { timeout: 30_000 });
    }
  }
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

async function runCoreJourney(
  page: Page,
  request: APIRequestContext,
  world: World,
  testInfo: TestInfo,
): Promise<void> {
  const issuePath = `/w/${world.workspace.slug}/issues/${world.issue.identifier}`;
  await page.goto(issuePath);
  await expect(page.getByTestId('comments-panel')).toBeVisible();
  await runOptimisticRetry(page, request, world);

  // Resolved thread can be reopened and resolved again with resolver trace.
  await page.getByTestId('resolved-threads-toggle').click();
  await clickCommentAction(page, world.rootComment.id, 'reopen');
  await expect(page.getByTestId(`comment-card-${world.rootComment.id}`)).toBeVisible();
  await clickCommentAction(page, world.rootComment.id, 'resolve');
  await expect(page.getByTestId('resolved-threads-toggle')).toBeVisible();

  // Approve the high-risk request, claim attempt #2, return real logs/usage,
  // and finish the logical execution. The issue must advance to review.
  await postData(
    request,
    `/api/v1/workspaces/${world.workspace.id}/approvals/${world.approval.id}/approve`,
    world.ownerToken,
    { comment: 'Approved in MES-188 real-stack acceptance.' },
    200,
  );
  await waitForExecutionStatus(request, world, world.primaryExecution.id, 'queued');
  const resumed = await claim(request, world.onlineRuntime);
  expect(resumed.execution.id).toBe(world.primaryExecution.id);
  expect(resumed.attempt.attempt_number).toBe(2);
  await transitionAttempt(request, world.onlineRuntime, resumed.attempt.id, 'running');
  await appendLogs(request, world.onlineRuntime, resumed.attempt.id, [
    'Approved action resumed',
    'MES-188 output ready for review',
  ]);
  await transitionAttempt(request, world.onlineRuntime, resumed.attempt.id, 'completed', {
    result: validResult('approved-output'),
  });
  const primaryDetail = await waitForExecutionStatus(
    request,
    world,
    world.primaryExecution.id,
    'completed',
  );
  expect(primaryDetail.attempts).toHaveLength(2);
  expect(primaryDetail.approval_audits).toHaveLength(1);
  await expect
    .poll(
      async () =>
        (await getData<Issue>(request, `/api/v1/issues/${world.issue.id}`, world.ownerToken))
          .state_category,
    )
    .toBe('in_review');

  await page.goto(issuePath);
  await expect(
    page.getByTestId(`issue-execution-status-${world.primaryExecution.id}`),
  ).toHaveAttribute('data-status', 'completed');
  const rejectResponse = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === 'PATCH' &&
      new URL(candidate.url()).pathname === `/api/v1/issues/${world.issue.id}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId(`issue-execution-reject-${world.primaryExecution.id}`).click();
  expect((await rejectResponse).status()).toBe(200);
  await expect(page.getByTestId('composer-input').last()).toBeFocused();

  // A rejected candidate is final audit evidence and cannot later be
  // approved. Produce a newer completed execution, then approve that exact
  // current candidate.
  const reviewExecution = await triggerExecution(
    request,
    world,
    'replacement output after requested changes',
    world.issue.id,
  );
  const reviewClaim = await claim(request, world.onlineRuntime);
  expect(reviewClaim.execution.id).toBe(reviewExecution.id);
  await transitionAttempt(request, world.onlineRuntime, reviewClaim.attempt.id, 'running');
  await transitionAttempt(request, world.onlineRuntime, reviewClaim.attempt.id, 'completed', {
    result: validResult('replacement-approved-output'),
  });
  await waitForExecutionStatus(request, world, reviewExecution.id, 'completed');
  await page.goto(issuePath);
  const doneResponse = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === 'PATCH' &&
      new URL(candidate.url()).pathname === `/api/v1/issues/${world.issue.id}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId(`issue-execution-approve-${reviewExecution.id}`).click();
  expect((await doneResponse).status()).toBe(200);
  await expect
    .poll(
      async () =>
        (await getData<Issue>(request, `/api/v1/issues/${world.issue.id}`, world.ownerToken))
          .state_category,
    )
    .toBe('done');

  await page.goto(`/w/${world.workspace.slug}/executions/${world.primaryExecution.id}`);
  await page.getByTestId('execution-tab-audit').click();
  await expect(page.locator('[data-testid^="execution-attempt-audit-"]')).toHaveCount(2);
  await expect(page.getByTestId(`execution-approval-audit-${world.approval.id}`)).toBeVisible();
  await capture(page, testInfo, 'core-completed-attempt-audit');

  // A real failed attempt emits one critical inbox item with a redacted tail
  // and execution deep link. No plaintext credential may survive.
  const failedExecution = await triggerExecution(request, world, 'produce a failure notification');
  const failedClaim = await claim(request, world.onlineRuntime);
  expect(failedClaim.execution.id).toBe(failedExecution.id);
  await transitionAttempt(request, world.onlineRuntime, failedClaim.attempt.id, 'running');
  await appendLogs(request, world.onlineRuntime, failedClaim.attempt.id, [
    'provider booted',
    `token=${REDACTION_SECRET}`,
    '\u001b[31mterminal provider failure\u001b[0m',
  ]);
  await transitionAttempt(request, world.onlineRuntime, failedClaim.attempt.id, 'failed', {
    failure_reason: 'executor_unavailable',
  });
  await waitForExecutionStatus(request, world, failedExecution.id, 'failed');
  let failureNotification: Notification | undefined;
  await expect
    .poll(
      async () => {
        const inbox = await getData<readonly Notification[]>(
          request,
          `/api/v1/inbox?workspace_id=${world.workspace.id}&filter=all&limit=100`,
          world.ownerToken,
        );
        failureNotification = inbox.find((item) => item.execution_id === failedExecution.id);
        return failureNotification?.id ?? null;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .not.toBeNull();
  expect(failureNotification?.type).toBe('execution_finished');
  expect(failureNotification?.priority).toBe('critical');
  expect(failureNotification?.preview).toContain('executor_unavailable');
  expect(failureNotification?.preview).toContain('terminal provider failure');
  expect(failureNotification?.preview).toContain('token=***');
  expect(failureNotification?.preview).not.toContain(REDACTION_SECRET);
  await page.goto(`/w/${world.workspace.slug}/inbox`);
  const inboxRow = page.getByTestId(`inbox-row-${failureNotification?.id ?? ''}`);
  await expect(inboxRow).toBeVisible();
  await inboxRow.locator('.mesh-inbox__row-main').click({
    position: { x: 24, y: 24 },
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page).toHaveURL(
    new RegExp(`/w/${world.workspace.slug}/inbox/${failureNotification?.id ?? ''}`),
  );
  await page.getByTestId('inbox-preview-open').click();
  await expect(page).toHaveURL(
    new RegExp(`/w/${world.workspace.slug}/executions/${failedExecution.id}`),
  );
  await expect(page.getByTestId('execution-panel-logs')).toContainText('terminal provider failure');

  // Stop action on the issue execution panel cancels an actual queued run.
  const stoppedExecution = await triggerExecution(request, world, 'stop this queued run');
  await page.goto(`/w/${world.workspace.slug}/issues/${world.lifecycleIssue.identifier}`);
  await expect(page.getByTestId(`issue-execution-cancel-${stoppedExecution.id}`)).toBeVisible();
  await page.getByTestId(`issue-execution-cancel-${stoppedExecution.id}`).click();
  await waitForExecutionStatus(request, world, stoppedExecution.id, 'cancelled');

  // pause(cancel_current) cancels queued work; resume makes the agent eligible.
  const pauseCancelled = await triggerExecution(request, world, 'cancel through agent pause');
  await page.goto(`/w/${world.workspace.slug}/agents/${world.agent.id}`);
  await page.getByTestId('agent-pause-button').click();
  await page.getByTestId('agent-pause-cancel').check();
  await page.getByTestId('agent-pause-reason').fill('MES-188 cancel-current acceptance');
  await page.getByTestId('agent-pause-confirm').click();
  await lifecycleStatus(request, world, 'paused');
  const cancelledByPause = await waitForExecutionStatus(
    request,
    world,
    pauseCancelled.id,
    'cancelled',
  );
  expect(cancelledByPause.failure_reason).toBe('agent_paused');
  await page.getByTestId('agent-resume-button').click();
  await lifecycleStatus(request, world, 'active');

  // pause(finish_current) preserves a running attempt; it is completed after
  // resume, proving the two policies are behaviorally distinct.
  const finishExecution = await triggerExecution(request, world, 'finish while paused');
  const finishClaim = await claim(request, world.onlineRuntime);
  expect(finishClaim.execution.id).toBe(finishExecution.id);
  await transitionAttempt(request, world.onlineRuntime, finishClaim.attempt.id, 'running');
  await page.goto(`/w/${world.workspace.slug}/agents/${world.agent.id}`);
  await page.getByTestId('agent-pause-button').click();
  await page.getByTestId('agent-pause-finish').check();
  await page.getByTestId('agent-pause-confirm').click();
  await lifecycleStatus(request, world, 'paused');
  expect((await getExecution(request, world, finishExecution.id)).status).toBe('running');
  await page.getByTestId('agent-resume-button').click();
  await lifecycleStatus(request, world, 'active');
  await transitionAttempt(request, world.onlineRuntime, finishClaim.attempt.id, 'completed', {
    result: validResult('finish-current'),
  });

  // Configuration compare/rollback, ownership transfer and every lifecycle
  // action are exercised through the rendered controls.
  await page.getByTestId('agent-tab-history').click();
  await expect(page.getByTestId(`agent-version-${world.secondConfigVersionId}`)).toBeVisible();
  await page.getByTestId(`agent-compare-${world.secondConfigVersionId}`).click();
  await expect(page.getByTestId(`agent-compare-body-${world.secondConfigVersionId}`)).toBeVisible();
  await page.getByTestId(`agent-rollback-${world.firstConfigVersionId}`).click();
  await expect(page.getByTestId('agent-panel-config')).toBeVisible();

  await page.getByTestId('agent-tab-visibility').click();
  await page.getByTestId('agent-transfer-button').click();
  await page.getByTestId('agent-transfer-user-id').fill(world.peerUserId);
  await page.getByTestId('agent-transfer-confirm').click();
  await expect(page.getByTestId('agent-transfer-dialog')).toHaveCount(0);

  await page.goto(`/w/${world.workspace.slug}/agents/${world.agent.id}`);
  await page.getByTestId('agent-disable-button').click();
  await lifecycleStatus(request, world, 'disabled');
  await page.getByTestId('agent-enable-button').click();
  await lifecycleStatus(request, world, 'active');
  await page.getByTestId('agent-archive-button').click();
  await lifecycleStatus(request, world, 'archived');
  await page.getByTestId('agent-restore-button').click();
  await lifecycleStatus(request, world, 'active');

  // Runtime pause/resume buttons use real console endpoints. Isolated state
  // exposes a downloadable redacted diagnostic and fixed re-register command.
  await page.goto(`/w/${world.workspace.slug}/automations/runtimes/${world.pausedRuntime.id}`);
  await page.getByTestId('runtime-detail-resume').click();
  await expect(page.getByTestId('runtime-detail-pause')).toBeVisible();
  await page.getByTestId('runtime-detail-pause').click();
  await expect(page.getByTestId('runtime-detail-resume')).toBeVisible();
  await page.goto(`/w/${world.workspace.slug}/automations/runtimes/${world.isolatedRuntime.id}`);
  const download = page.waitForEvent('download');
  await page.getByTestId('runtime-export-diagnostics').click();
  expect((await download).suggestedFilename()).toMatch(/runtime.*diagnostics.*\.json/i);
  await expect(page.getByTestId('runtime-reregister-command')).toContainText('mesh-runtime');

  // Soft deletion keeps the agent-authored historical comment but renders an
  // inactive identity rather than a broken/null author.
  const removed = await request.delete(
    `/api/v1/workspaces/${world.workspace.id}/agents/${world.agent.id}`,
    { headers: { Authorization: `Bearer ${world.ownerToken}` } },
  );
  expect(removed.status(), await removed.text()).toBe(204);
  await page.goto(issuePath);
  await expect(page.getByText(/Inactive agent|已停用 Agent/i).first()).toBeVisible();

  const dbEvidence = psqlJson<Record<string, unknown>>(`
    SELECT json_build_object(
      'primary_attempts', (
        SELECT count(*) FROM execution_attempts
        WHERE execution_id = '${world.primaryExecution.id}'::uuid
      ),
      'approval_status', (
        SELECT status FROM approvals WHERE id = '${world.approval.id}'::uuid
      ),
      'reply_count', (
        SELECT count(*) FROM comments WHERE thread_root_id = '${world.rootComment.id}'::uuid
      ),
      'tombstone_persisted', (
        SELECT deleted_at IS NOT NULL FROM comments WHERE id = '${world.tombstone.id}'::uuid
      ),
      'agent_member_removed', (
        SELECT status = 'removed' FROM members WHERE agent_id = '${world.agent.id}'::uuid
      ),
      'failed_notification_count', (
        SELECT count(*) FROM notifications
        WHERE workspace_id = '${world.workspace.id}'::uuid
          AND type = 'execution_finished'
          AND execution_id = '${failedExecution.id}'::uuid
      ),
      'rejected_output_review_count', (
        SELECT count(*) FROM issue_activity
        WHERE issue_id = '${world.issue.id}'::uuid
          AND field = 'execution_output_review'
          AND new_value->>'execution_id' = '${world.primaryExecution.id}'
          AND new_value->>'decision' = 'rejected'
      ),
      'approved_output_review_count', (
        SELECT count(*) FROM issue_activity
        WHERE issue_id = '${world.issue.id}'::uuid
          AND field = 'execution_output_review'
          AND new_value->>'execution_id' = '${reviewExecution.id}'
          AND new_value->>'decision' = 'approved'
      )
    );
  `);
  expect(dbEvidence).toEqual({
    primary_attempts: 2,
    approval_status: 'approved',
    reply_count: 1,
    tombstone_persisted: true,
    agent_member_removed: true,
    failed_notification_count: 1,
    rejected_output_review_count: 1,
    approved_output_review_count: 1,
  });
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await writeFile(
    join(EVIDENCE_DIR, 'real-stack-contract.json'),
    `${JSON.stringify(
      {
        verdict: 'PASS',
        workspace_id: world.workspace.id,
        issue_id: world.issue.id,
        execution_id: world.primaryExecution.id,
        failed_execution_id: failedExecution.id,
        runtime_states: ['online', 'degraded', 'paused', 'isolated'],
        database: dbEvidence,
        optimistic_retry: { persisted_rows: 1, stable_idempotency_key: true },
        provider_credentials_redacted: true,
      },
      null,
      2,
    )}\n`,
    'utf8',
  );
}

test.describe.configure({ mode: 'serial' });

test('agent/runtime/comment execution matrix on the real stack', async ({
  page,
  request,
}, testInfo) => {
  const world = await seedWorld(request, testInfo.project.name);
  await loginThroughUi(page, world.ownerEmail, world.ownerPassword);
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  await setTheme(page, theme);

  // Assignment materialization is immediately visible on the real board.
  await page.goto(`/w/${world.workspace.slug}/board`);
  await focusBoardLane(page, world.issue.id);
  await expect(page.getByTestId(`board-card-${world.issue.id}`)).toBeVisible();
  await expect(page.getByTestId(`board-card-execution-${world.issue.id}`)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'board-processing');

  // REST-first capacity snapshot: awaiting approval is a distinct third count.
  await page.goto(`/w/${world.workspace.slug}/agents/${world.agent.id}`);
  await expect(page.getByTestId('agent-detail-presence-caption')).toContainText('1');
  const agentDetail = await getData<Agent>(
    request,
    `/api/v1/workspaces/${world.workspace.id}/agents/${world.agent.id}`,
    world.ownerToken,
  );
  expect(agentDetail.capacity).toEqual({ running: 0, queued: 0, awaiting_approval: 1 });
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'agent-capacity');

  // Resolved area starts folded; one-level replies are folded independently;
  // tombstones and resolver/time trace survive a fresh REST load.
  await page.goto(`/w/${world.workspace.slug}/issues/${world.issue.identifier}`);
  await expect(page.getByTestId('resolved-threads-toggle')).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await expect(page.getByTestId(`comment-deleted`)).toBeVisible();
  await page.getByTestId('resolved-threads-toggle').click();
  await expect(page.getByTestId(`comment-resolved-meta`)).toBeVisible();
  await expect(page.getByTestId(`thread-toggle-${world.rootComment.id}`)).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await page.getByTestId(`thread-toggle-${world.rootComment.id}`).click();
  await expect(page.getByTestId(`comment-card-${world.reply.id}`)).toBeVisible();
  await expect(
    page.getByTestId(`issue-execution-runtime-${world.primaryExecution.id}`),
  ).toContainText('mes188-online');
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'comments-and-issue-executions');

  if (testInfo.project.name === 'desktop-light') {
    const browser = page.context().browser();
    expect(browser).not.toBeNull();
    const peerContext = await browser!.newContext({ baseURL: new URL(page.url()).origin });
    try {
      const peerPage = await peerContext.newPage();
      await loginThroughUi(peerPage, world.peerEmail, world.peerPassword);
      await verifyTwoAccountRealtimeAndPrivacy(page, peerPage, request, world);
    } finally {
      await peerContext.close();
    }
  }

  // Actionable degraded state exposes exact capability/task impact and a
  // server-derived repair command. Isolated/paused/online are seeded through
  // the same daemon heartbeat/console APIs and asserted via their own details.
  await page.goto(`/w/${world.workspace.slug}/automations/runtimes/${world.degradedRuntime.id}`);
  await expect(page.getByTestId('runtime-operational-state')).toContainText(/Degraded|降级/i);
  await expect(page.getByTestId('runtime-diagnostic-provider_unavailable')).toContainText(
    'claude-code',
  );
  await expect(page.getByTestId('runtime-diagnostic-copy-provider_unavailable')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'runtime-degraded');

  for (const [runtime, expected] of [
    [world.onlineRuntime, /Online|在线/i],
    [world.pausedRuntime, /Paused|暂停/i],
    [world.isolatedRuntime, /Isolated|隔离/i],
  ] as const) {
    await page.goto(`/w/${world.workspace.slug}/automations/runtimes/${runtime.id}`);
    await expect(page.getByTestId('runtime-operational-state')).toContainText(expected);
  }

  // The pending approval already supplies provider/version/model, frozen
  // budget, full timeline and request audit on every viewport/theme.
  await page.goto(`/w/${world.workspace.slug}/executions/${world.primaryExecution.id}`);
  await page.getByTestId('execution-tab-audit').click();
  await expect(
    page.getByTestId(`execution-attempt-audit-${world.primaryAttempt.id}`),
  ).toContainText('claude-code');
  await expect(page.getByTestId(`execution-approval-audit-${world.approval.id}`)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'attempt-audit');

  if (testInfo.project.name === 'desktop-light') {
    await runCoreJourney(page, request, world, testInfo);
  }
});
