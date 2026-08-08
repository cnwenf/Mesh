/**
 * MES-189 production-shaped four-combo evidence walkthrough
 * (desktop/phone x light/dark).
 *
 * No route interception and no fixture server: every request crosses the
 * loopback nginx front door into FastAPI, PostgreSQL, Redis, MinIO, the
 * outbox worker and the realtime gateway. The world is seeded over the same
 * public HTTP surfaces the browser uses (registration, invitations, agent
 * assignment, the daemon claim/approval protocol, inbox read/archive, squads
 * and skills), then the journey captures the slice's representative new
 * surfaces: board execution materialization, inbox inline approval, the
 * archived inbox view, the squad archive export entry, the data export
 * dialog, member presence and the skill bulk-bind dialog.
 */
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page, TestInfo } from '@playwright/test';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = resolve(HERE, '../../docs/evidence/mes-189');
const RESPONSE_TIMEOUT = 30_000;
const WORKER_TIMEOUT = 90_000;
const PROBE_COMMENT = 'MES-189 archive probe comment';

interface Envelope<T> {
  readonly data: T;
}

interface Workspace {
  readonly id: string;
  readonly slug: string;
}

interface UserProfile {
  readonly user: { readonly id: string };
}

interface MemberRecord {
  readonly id: string;
  readonly member_type: string;
  readonly profile: { readonly email?: string | null } | null;
}

interface Agent {
  readonly id: string;
  readonly name: string;
  readonly active_config_version_id: string;
  readonly member: { readonly id: string };
}

interface Issue {
  readonly id: string;
  readonly identifier: string;
}

interface RuntimeRecord {
  readonly id: string;
}

interface ActivatedRuntime {
  readonly id: string;
  readonly token: string;
}

interface Attempt {
  readonly id: string;
}

interface Execution {
  readonly id: string;
  readonly status: string;
}

interface ApprovalRecord {
  readonly id: string;
  readonly status: string;
}

interface InboxItem {
  readonly id: string;
  readonly type: string;
  readonly issue_id: string | null;
  readonly approval_id?: string | null;
  readonly payload?: Record<string, unknown>;
  readonly preview?: string | null;
}

interface SquadRecord {
  readonly id: string;
}

interface SquadTaskRecord {
  readonly id: string;
}

interface SquadMessageRecord {
  readonly id: string;
}

interface SkillRecord {
  readonly id: string;
}

interface SkillVersionRecord {
  readonly id: string;
}

interface World {
  readonly ownerEmail: string;
  readonly ownerPassword: string;
  readonly ownerToken: string;
  readonly ownerMemberId: string;
  readonly peerEmail: string;
  readonly peerPassword: string;
  readonly peerToken: string;
  readonly workspace: Workspace;
  readonly agent: Agent;
  readonly secondAgent: Agent;
  readonly runtime: ActivatedRuntime;
  readonly issue: Issue;
  readonly execution: Execution;
  readonly approval: ApprovalRecord;
  readonly approvalNotificationId: string;
  readonly archivedNotificationId: string;
  readonly squad: SquadRecord;
  readonly directiveMessage: SquadMessageRecord;
  readonly skill: SkillRecord;
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
): Promise<ActivatedRuntime> {
  const created = await postData<
    RuntimeRecord & { readonly activation: { readonly code: string } }
  >(request, `/api/v1/workspaces/${workspaceId}/runtimes`, ownerToken, {
    name,
    kind: 'self_hosted',
    labels: { suite: 'mes189' },
    max_concurrent: 2,
  });
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
        version: '0.3.0-mes189',
      },
      protocol_version: 1,
      provider_manifest: {
        provider: 'claude-code',
        version: '2.1.218',
        model: 'mes189-e2e-model',
      },
      daemon_features: { sandbox: 'linux_ns', broker: 'unix', egress: 'gateway' },
    },
    200,
  );
  await postData(
    request,
    `/api/v1/daemon/runtimes/${created.id}:heartbeat`,
    activated.runtime_token,
    {
      current_load: 0,
      health: 'healthy',
      operational_state: 'online',
      diagnostics: [],
      metrics: { source: 'mes189-real' },
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
): Promise<Execution> {
  let found: Execution | undefined;
  await expect
    .poll(
      async () => {
        const executions = await listIssueExecutions(request, world, issueId);
        found = executions[0];
        return found?.id ?? null;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .not.toBeNull();
  return found as Execution;
}

async function waitForExecutionStatus(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  executionId: string,
  expected: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const detail = await getData<Execution>(
          request,
          `/api/v1/workspaces/${world.workspace.id}/executions/${executionId}`,
          world.ownerToken,
        );
        return detail.status;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .toBe(expected);
}

async function listInbox(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
): Promise<readonly InboxItem[]> {
  return getData<readonly InboxItem[]>(
    request,
    `/api/v1/inbox?workspace_id=${world.workspace.id}&limit=100`,
    world.ownerToken,
  );
}

async function waitForInboxItem(
  request: APIRequestContext,
  world: Pick<World, 'workspace' | 'ownerToken'>,
  matches: (item: InboxItem) => boolean,
): Promise<InboxItem> {
  let found: InboxItem | undefined;
  await expect
    .poll(
      async () => {
        const items = await listInbox(request, world);
        found = items.find(matches);
        return found?.id ?? null;
      },
      { timeout: WORKER_TIMEOUT },
    )
    .not.toBeNull();
  return found as InboxItem;
}

function approvalIdOf(item: InboxItem): string | null {
  if (typeof item.approval_id === 'string' && item.approval_id.length > 0) {
    return item.approval_id;
  }
  const payloadApproval = item.payload?.approval_id;
  return typeof payloadApproval === 'string' && payloadApproval.length > 0 ? payloadApproval : null;
}

async function seedWorld(request: APIRequestContext, label: string): Promise<World> {
  const suffix = `${label}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
    .replaceAll('_', '-')
    .toLowerCase();
  const ownerEmail = `mes189-owner-${suffix}@example.com`;
  const peerEmail = `mes189-peer-${suffix}@example.com`;
  const ownerPassword = `Mesh#Owner-${suffix}A9!`;
  const peerPassword = `Mesh#Peer-${suffix}B8!`;
  const owner = await registerAndLogin(request, ownerEmail, ownerPassword, 'MES-189 Owner');
  const peer = await registerAndLogin(request, peerEmail, peerPassword, 'MES-189 Peer');
  const workspace = await postData<Workspace>(request, '/api/v1/workspaces', owner.token, {
    name: `MES-189 ${label}`,
    slug: `m189-${suffix}`.slice(0, 32),
  });
  await postData(request, `/api/v1/workspaces/${workspace.id}/views`, owner.token, {
    name: 'MES-189 Board',
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

  const roster = await getData<readonly MemberRecord[]>(
    request,
    `/api/v1/workspaces/${workspace.id}/members`,
    owner.token,
  );
  const ownerMember = roster.find((member) => member.profile?.email === ownerEmail);
  expect(ownerMember, 'owner member in roster').toBeTruthy();

  const agentShape = {
    role_tag: 'Runtime operator',
    system_instructions: 'Return concise test evidence.',
    trigger_on_assign: true,
    model_config: {
      provider: 'claude-code',
      model: 'mes189-e2e-model',
      model_tier: 'balanced',
      temperature: 0.2,
      max_tokens: 4096,
      budget: { max_cost_usd: '0.50', max_turns: 3, max_tokens: 4096 },
    },
  };
  const agent = await postData<Agent>(
    request,
    `/api/v1/workspaces/${workspace.id}/agents`,
    owner.token,
    { name: `MES-189 Agent ${label}`, ...agentShape },
  );
  const secondAgent = await postData<Agent>(
    request,
    `/api/v1/workspaces/${workspace.id}/agents`,
    owner.token,
    { name: `MES-189 Second ${label}`, ...agentShape },
  );

  const runtime = await createAndActivateRuntime(
    request,
    workspace.id,
    owner.token,
    `mes189-online-${suffix}`,
  );

  // Assignment to the agent member materializes a real execution through the
  // outbox worker; claiming and requesting approval drives the daemon
  // protocol over its public HTTP surface, which fans a review_requested
  // notification with approval_id into the owner inbox.
  const issue = await postData<Issue>(
    request,
    `/api/v1/workspaces/${workspace.id}/issues`,
    owner.token,
    {
      title: `MES-189 approval showcase ${label}`,
      description: 'Real assignment, claim, approval and inbox fan-out.',
      assignee_id: agent.member.id,
    },
  );
  const baseWorld = { workspace, ownerToken: owner.token };
  const execution = await waitForExecution(request, baseWorld, issue.id);
  let claimResponse = await request.post(`/api/v1/daemon/runtimes/${runtime.id}/executions:claim`, {
    headers: { Authorization: `Bearer ${runtime.token}` },
    data: { diagnostics: {} },
  });
  await expect
    .poll(
      async () => {
        if (claimResponse.status() !== 200) {
          claimResponse = await request.post(
            `/api/v1/daemon/runtimes/${runtime.id}/executions:claim`,
            {
              headers: { Authorization: `Bearer ${runtime.token}` },
              data: { diagnostics: {} },
            },
          );
        }
        return claimResponse.status();
      },
      { timeout: WORKER_TIMEOUT },
    )
    .toBe(200);
  const claimed = await dataOf<{ readonly execution: Execution; readonly attempt: Attempt }>(
    claimResponse,
  );
  expect(claimed.execution.id).toBe(execution.id);
  const attemptPatch = await request.patch(`/api/v1/daemon/attempts/${claimed.attempt.id}`, {
    headers: { Authorization: `Bearer ${runtime.token}` },
    data: { lease_seq: 1, status: 'running' },
  });
  expect(attemptPatch.status(), await attemptPatch.text()).toBe(200);
  const approval = await postData<ApprovalRecord>(
    request,
    `/api/v1/daemon/executions/${execution.id}/approvals`,
    runtime.token,
    {
      lease_seq: 1,
      attempt_id: claimed.attempt.id,
      action_summary: { action: 'exec:shell', command_class: 'write', target: 'workspace' },
      resume_context: { checkpoint_ref: 'mes189-checkpoint-1' },
    },
    200,
  );
  await waitForExecutionStatus(request, baseWorld, execution.id, 'awaiting_approval');

  // The peer comment fans out a second notification; it becomes the archived
  // row after a real mark-read + archive round trip.
  await postData(request, `/api/v1/issues/${issue.id}/comments`, peer.token, {
    body_markdown: PROBE_COMMENT,
  });

  const approvalNotification = await waitForInboxItem(
    request,
    baseWorld,
    (item) => approvalIdOf(item) === approval.id,
  );
  const commentNotification = await waitForInboxItem(request, baseWorld, (item) =>
    JSON.stringify(item).includes(PROBE_COMMENT),
  );
  await postData(
    request,
    `/api/v1/inbox/${commentNotification.id}/read?workspace_id=${workspace.id}`,
    owner.token,
    {},
    200,
  );
  await postData(
    request,
    `/api/v1/inbox/${commentNotification.id}/archive?workspace_id=${workspace.id}`,
    owner.token,
    {},
    200,
  );

  // Squad with a linked task and kind-colored messages (directive/report
  // carry the related-task chip).
  const squad = await postData<SquadRecord>(
    request,
    `/api/v1/workspaces/${workspace.id}/squads`,
    owner.token,
    {
      name: `MES-189 Squad ${label}`,
      members: [{ member_id: (ownerMember as MemberRecord).id, role: 'leader' }],
    },
  );
  const squadTask = await postData<SquadTaskRecord>(
    request,
    `/api/v1/workspaces/${workspace.id}/squads/${squad.id}/tasks`,
    owner.token,
    { issue_id: issue.id },
    202,
  );
  const directiveMessage = await postData<SquadMessageRecord>(
    request,
    `/api/v1/workspaces/${workspace.id}/squads/${squad.id}/messages`,
    owner.token,
    {
      kind: 'instruction',
      body_markdown: 'Investigate the approval showcase.',
      task_id: squadTask.id,
    },
  );
  await postData(
    request,
    `/api/v1/workspaces/${workspace.id}/squads/${squad.id}/messages`,
    owner.token,
    { kind: 'report', body_markdown: 'Findings archived and exported.', task_id: squadTask.id },
  );
  await postData(
    request,
    `/api/v1/workspaces/${workspace.id}/squads/${squad.id}/messages`,
    owner.token,
    { kind: 'chat', body_markdown: 'Standing by for review.' },
  );

  // Installed skill ready for the bulk-bind dialog (two-agent roster).
  const skill = await postData<SkillRecord>(
    request,
    `/api/v1/workspaces/${workspace.id}/skills`,
    owner.token,
    { name: `mes189-${suffix}`, summary: 'MES-189 walkthrough skill' },
  );
  const version = await postData<SkillVersionRecord>(
    request,
    `/api/v1/workspaces/${workspace.id}/skills/${skill.id}/versions`,
    owner.token,
    { version: '1.0.0', instructions: 'Return concise evidence.', publish: true },
  );
  await postData(request, `/api/v1/workspaces/${workspace.id}/skill-installations`, owner.token, {
    skill_id: skill.id,
    skill_version_id: version.id,
    scope: 'workspace',
  });

  return {
    ownerEmail,
    ownerPassword,
    ownerToken: owner.token,
    ownerMemberId: (ownerMember as MemberRecord).id,
    peerEmail,
    peerPassword,
    peerToken: peer.token,
    workspace,
    agent,
    secondAgent,
    runtime,
    issue,
    execution,
    approval,
    approvalNotificationId: approvalNotification.id,
    archivedNotificationId: commentNotification.id,
    squad,
    directiveMessage,
    skill,
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

async function dismissOnboardingChecklist(page: Page): Promise<void> {
  const dismiss = page.getByTestId('onboarding-dismiss');
  try {
    // The checklist loads async after the shell; give it a moment to appear.
    await dismiss.waitFor({ state: 'visible', timeout: 10_000 });
  } catch {
    return; // already dismissed or not rendered on this page
  }
  await dismiss.click();
  await expect(page.getByTestId('onboarding-card')).toHaveCount(0);
}

async function capture(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({
    path: join(EVIDENCE_DIR, `${testInfo.project.name}-${name}.png`),
    fullPage: true,
  });
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const element = document.documentElement;
    return element.scrollWidth - element.clientWidth;
  });
  expect(overflow, 'horizontal overflow px').toBeLessThanOrEqual(1);
}

// Narrow viewports render the board as a one-lane tab strip (the default
// selected column may not hold the seeded card); desktop shows every lane at
// once and returns on the first visibility check.
async function focusBoardLane(page: Page, issueId: string): Promise<void> {
  const card = page.getByTestId(`board-card-${issueId}`);
  const tabs = page.getByRole('tablist', { name: 'Board columns' }).getByRole('tab');
  const deadline = Date.now() + RESPONSE_TIMEOUT;
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

test('MES-189 new surfaces across desktop/phone and light/dark', async ({
  page,
  request,
}, testInfo) => {
  const world = await seedWorld(request, testInfo.project.name);
  await loginThroughUi(page, world.ownerEmail, world.ownerPassword);
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  await setTheme(page, theme);
  const slug = world.workspace.slug;

  // Assignment materialization is immediately visible on the real board.
  await page.goto(`/w/${slug}/board`);
  // The onboarding checklist sits above the outlet inside the scrollable
  // main, so a fresh deep link leaves the page content below the fold of
  // the capture. Real users dismiss it before working; do the same so each
  // scene shows the surface under test.
  await dismissOnboardingChecklist(page);
  await focusBoardLane(page, world.issue.id);
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'board');

  // Inbox: the review_requested row renders inline approve/reject actions;
  // approving through the UI drives the real approval endpoint. Row actions
  // are a hover/focus overlay on desktop, so hover the row before asserting
  // and clicking (mobile viewports render them always-visible).
  await page.goto(`/w/${slug}/inbox`);
  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  const approvalRow = page.getByTestId(`inbox-row-${world.approvalNotificationId}`);
  await expect(approvalRow).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  const approveButton = page.getByTestId(`inbox-approval-approve-${world.approval.id}`);
  await expect(approveButton).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await expect(page.getByTestId(`inbox-approval-reject-${world.approval.id}`)).toBeVisible();
  await approvalRow.hover();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'inbox-inline-approval');
  await approveButton.click();
  await expect(page.getByTestId(`inbox-approval-decided-${world.approval.id}`)).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });

  // Archived view (deep link) surfaces the archived row and hides it from
  // the main list.
  await page.goto(`/w/${slug}/inbox?filter=archived`);
  await expect(page.getByTestId(`inbox-row-${world.archivedNotificationId}`)).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page.getByTestId(`inbox-row-${world.approvalNotificationId}`)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'inbox-archived');

  // Squad detail: kind-colored messages with related-task chips, and the
  // header overflow menu exposes the real archive export entry.
  await page.goto(`/w/${slug}/squads/${world.squad.id}`);
  await expect(page.getByTestId('squad-messages-pane')).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page.getByTestId(`squad-message-${world.directiveMessage.id}`)).toBeVisible();
  await expect(page.getByTestId(`squad-message-task-${world.directiveMessage.id}`)).toBeVisible();
  await page.getByRole('button', { name: 'Squad actions' }).click();
  const exportItem = page.getByRole('menuitem', { name: 'Export archive' });
  await expect(exportItem).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'squad-export-menu');
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: RESPONSE_TIMEOUT }),
    exportItem.click(),
  ]);
  expect(download.suggestedFilename()).toContain(world.squad.id);
  expect(download.suggestedFilename()).toMatch(/\.md$/);

  // Data management: the export dialog opens with scope/format controls.
  await page.goto(`/w/${slug}/settings/data`);
  await page.getByTestId('open-export-dialog').click({ timeout: RESPONSE_TIMEOUT });
  await expect(page.getByTestId('export-scope-select')).toBeVisible();
  await expect(page.getByTestId('export-format-select')).toBeVisible();
  await expect(page.getByTestId('export-submit-button')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'data-export-dialog');
  await page.keyboard.press('Escape');

  // Members: the owner's realtime presence renders an online indicator.
  // Desktop rows and mobile cards each carry their own dot; CSS shows one per
  // breakpoint, so assert whichever variant is visible on this viewport.
  await page.goto(`/w/${slug}/members`);
  const desktopDot = page.getByTestId(`member-online-${world.ownerMemberId}`);
  const cardDot = page.getByTestId(`card-member-online-${world.ownerMemberId}`);
  await expect
    .poll(async () => (await desktopDot.isVisible()) || (await cardDot.isVisible()), {
      timeout: WORKER_TIMEOUT,
    })
    .toBe(true);
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'members-presence');

  // Skill detail: bulk-bind dialog lists the active agent roster.
  await page.goto(`/w/${slug}/automations/skills/${world.skill.id}`);
  await page.getByRole('button', { name: 'Bind to agents…' }).click({
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page.getByTestId('bulk-bind-body')).toBeVisible();
  await expect(page.getByTestId(`bulk-bind-agent-${world.agent.id}`)).toBeVisible();
  await expect(page.getByTestId(`bulk-bind-agent-${world.secondAgent.id}`)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, 'skill-bulk-bind');
});
