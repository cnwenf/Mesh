/**
 * MES-187 production-stack acceptance.
 *
 * Every browser talks to the same-origin nginx frontend backed by the real API,
 * worker, PostgreSQL, and Redis. The desktop/light project executes the full
 * mutation journey; all four desktop/phone × light/dark projects operate the
 * representative board, member drawer, and permanent-key disclosure.
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page, TestInfo } from '@playwright/test';

const PG_CONTAINER = process.env.MES187_PG_CONTAINER ?? 'mes187-real-postgres-1';
const RESPONSE_TIMEOUT = 30_000;
const EVIDENCE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), 'evidence/mes187');
mkdirSync(EVIDENCE_DIR, { recursive: true });

interface Envelope<T> {
  readonly data: T;
}

interface WorkspaceData {
  readonly id: string;
  readonly slug: string;
}

interface LabelData {
  readonly id: string;
  readonly name: string;
  readonly color: string;
}

interface IssueData {
  readonly id: string;
  readonly identifier: string;
  readonly version: number;
  readonly assignee_id: string | null;
}

interface StatusData {
  readonly id: string;
  readonly name: string;
  readonly category: string;
}

interface AgentData {
  readonly id: string;
  readonly member: { readonly id: string };
}

interface ProjectData {
  readonly id: string;
  readonly name: string;
  readonly key: string;
  readonly updated_at: string;
}

interface ViewData {
  readonly id: string;
}

interface UserData {
  readonly id: string;
  readonly avatar_url: string | null;
}

interface MeData {
  readonly user: UserData;
}

interface SeedData {
  readonly token: string;
  readonly workspace: WorkspaceData;
  readonly user: UserData;
  readonly source: LabelData;
  readonly target: LabelData;
  readonly extras: readonly LabelData[];
  readonly primaryIssue: IssueData;
  readonly stateIssue: IssueData;
  readonly statuses: {
    readonly todo: StatusData;
    readonly inProgress: StatusData;
    readonly done: StatusData;
  };
  readonly agent: AgentData;
  readonly project: ProjectData;
  readonly view: ViewData;
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

async function api<T>(
  request: APIRequestContext,
  token: string,
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE',
  path: string,
  data?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await request.fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...headers },
    ...(data === undefined ? {} : { data }),
  });
  if (!response.ok()) {
    throw new Error(
      `${method} ${path} returned ${String(response.status())}: ${await response.text()}`,
    );
  }
  return dataOf<T>(response);
}

async function registerLoginAndWorkspace(
  page: Page,
  request: APIRequestContext,
  projectName: string,
): Promise<{ token: string; workspace: WorkspaceData; user: UserData }> {
  const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const email = `mes187-${projectName}-${nonce}@example.com`;
  const password = `Mesh#${nonce}A9!`;
  const slug = `m187-${projectName}-${nonce}`.replaceAll(/[^a-z0-9-]/g, '-').slice(0, 32);

  const registration = await request.post('/api/v1/auth/register', {
    data: { email, password, display_name: `MES-187 ${projectName}` },
  });
  expect([200, 201]).toContain(registration.status());

  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(password);
  const loginPromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/auth/login',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('login-account-submit').click();
  const login = await loginPromise;
  expect(login.status(), await login.text()).toBe(200);
  const token = (await dataOf<{ access_token: string }>(login)).access_token;
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: RESPONSE_TIMEOUT });

  const workspace = await api<WorkspaceData>(request, token, 'POST', '/api/v1/workspaces', {
    name: `MES-187 ${projectName}`,
    slug,
    settings: { status_strict_mode: true },
  });
  const me = await api<MeData>(request, token, 'GET', '/api/v1/users/me');
  return { token, workspace, user: me.user };
}

function statusByCategory(statuses: readonly StatusData[], category: string): StatusData {
  const status = statuses.find((candidate) => candidate.category === category);
  if (status === undefined) throw new Error(`missing default status category ${category}`);
  return status;
}

async function createLabel(
  request: APIRequestContext,
  token: string,
  workspaceId: string,
  name: string,
  color: string,
): Promise<LabelData> {
  return api<LabelData>(request, token, 'POST', `/api/v1/workspaces/${workspaceId}/labels`, {
    name,
    color,
  });
}

async function seed(page: Page, request: APIRequestContext, testInfo: TestInfo): Promise<SeedData> {
  const { token, workspace, user } = await registerLoginAndWorkspace(
    page,
    request,
    testInfo.project.name,
  );
  const suffix = Math.random().toString(36).slice(2, 7);

  const source = await createLabel(request, token, workspace.id, `source-${suffix}`, '#e5484d');
  const target = await createLabel(request, token, workspace.id, `target-${suffix}`, '#3e63dd');
  const extras = await Promise.all([
    createLabel(request, token, workspace.id, `accessibility-${suffix}`, '#30a46c'),
    createLabel(request, token, workspace.id, `frontend-${suffix}`, '#f5a623'),
    createLabel(request, token, workspace.id, `security-${suffix}`, '#8e4ec6'),
  ]);

  const primaryIssue = await api<IssueData>(
    request,
    token,
    'POST',
    `/api/v1/workspaces/${workspace.id}/issues`,
    { title: `Projection issue ${suffix}` },
  );
  for (const label of [source, ...extras]) {
    await api(request, token, 'POST', `/api/v1/issues/${primaryIssue.id}/labels/${label.id}`);
  }

  const statuses = await api<StatusData[]>(
    request,
    token,
    'GET',
    `/api/v1/workspaces/${workspace.id}/statuses`,
  );
  const todo = statusByCategory(statuses, 'todo');
  const inProgress = statusByCategory(statuses, 'in_progress');
  const done = statusByCategory(statuses, 'done');
  await api(request, token, 'PATCH', `/api/v1/statuses/${todo.id}`, {
    allowed_transitions: [done.id],
  });
  await api(request, token, 'POST', `/api/v1/workspaces/${workspace.id}/custom-fields`, {
    name: `Release note ${suffix}`,
    field_key: `release_note_${suffix}`,
    type: 'text',
    is_required: true,
    required_on: ['status:done'],
  });
  const stateIssue = await api<IssueData>(
    request,
    token,
    'POST',
    `/api/v1/workspaces/${workspace.id}/issues`,
    { title: `Strict transition ${suffix}`, status_id: todo.id },
  );

  const agent = await api<AgentData>(
    request,
    token,
    'POST',
    `/api/v1/workspaces/${workspace.id}/agents`,
    {
      name: `Verifier ${suffix}`,
      role_tag: 'Release verifier',
      bio: 'Can verify releases and diagnose failures',
      system_instructions: 'Verify changes carefully.',
      model_config: { model_tier: 'balanced', temperature: 0.2, max_tokens: 2048 },
    },
  );
  const key = `K${suffix.toUpperCase()}`.slice(0, 12);
  const project = await api<ProjectData>(
    request,
    token,
    'POST',
    `/api/v1/workspaces/${workspace.id}/projects`,
    { name: `Permanent key ${suffix}`, key, visibility: 'public' },
  );
  const view = await api<ViewData>(
    request,
    token,
    'POST',
    `/api/v1/workspaces/${workspace.id}/views`,
    {
      name: `Labels ${suffix}`,
      layout: 'board',
      visibility: 'shared',
      group_by: 'label',
      board_settings: { card_fields: ['labels'] },
    },
  );
  return {
    token,
    workspace,
    user,
    source,
    target,
    extras,
    primaryIssue,
    stateIssue,
    statuses: { todo, inProgress, done },
    agent,
    project,
    view,
  };
}

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await page.evaluate(() => window.scrollTo(0, 0));
  const body = await page.screenshot({
    fullPage: false,
    path: resolve(EVIDENCE_DIR, `${testInfo.project.name}-${name}.png`),
  });
  await testInfo.attach(`${testInfo.project.name}-${name}`, { body, contentType: 'image/png' });
}

async function openMemberDrawer(page: Page, memberId: string): Promise<void> {
  const desktop = page.getByTestId(`member-open-${memberId}`);
  const mobile = page.getByTestId(`member-card-open-${memberId}`);
  const trigger = (page.viewportSize()?.width ?? 0) >= 768 ? desktop : mobile;
  await expect(trigger).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await trigger.click();
  await expect(page.getByTestId('member-drawer')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
}

async function runRepresentativeMatrix(
  page: Page,
  seedData: SeedData,
  testInfo: TestInfo,
): Promise<void> {
  const { workspace, view, primaryIssue, agent, project } = seedData;
  const expectsDark = testInfo.project.name.endsWith('dark');
  expect(await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches)).toBe(
    expectsDark,
  );

  await page.goto(`/w/${workspace.slug}/views/${view.id}`);
  if (testInfo.project.name.startsWith('phone-')) {
    await expect(page.getByTestId('board-compact')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
    await page.getByTestId(`compact-chip-${seedData.source.id}`).click();
  } else {
    await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  }
  await expect(page.getByTestId(`board-column-${seedData.source.id}`)).toBeVisible();
  await expect(
    page
      .getByTestId(`board-column-${seedData.source.id}`)
      .getByTestId(`board-card-${primaryIssue.id}`),
  ).toBeVisible();
  await attachScreenshot(page, testInfo, 'label-board');

  await page.goto(`/w/${workspace.slug}/members`);
  await openMemberDrawer(page, agent.member.id);
  await expect(page.getByTestId('member-detail-runtime')).toBeVisible();
  await expect(page.getByTestId('member-detail-model-tier')).toHaveText('Balanced', {
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page.getByText('No open assigned issues.')).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  await attachScreenshot(page, testInfo, 'member-drawer');

  await page.goto(`/w/${workspace.slug}/projects/${project.id}/settings`);
  await expect(page.getByTestId('settings-form')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await page.getByTestId('settings-delete').click();
  await expect(page.getByTestId('settings-delete-confirm-text')).toContainText(project.key);
  await expect(page.getByTestId('settings-delete-confirm-text')).toContainText(/reserved forever/i);
  await attachScreenshot(page, testInfo, 'project-key-disclosure');
}

test('business-depth real journey and four-combination review', async ({
  page,
  request,
}, testInfo) => {
  const data = await seed(page, request, testInfo);
  await runRepresentativeMatrix(page, data, testInfo);

  if (testInfo.project.name !== 'desktop-light') return;

  const {
    token,
    workspace,
    source,
    target,
    primaryIssue,
    stateIssue,
    statuses,
    agent,
    project,
    view,
  } = data;
  const auth = { Authorization: `Bearer ${token}` };

  // Dynamic label values appear without a page reload, and view-scoped quick
  // create persists the label association atomically.
  await page.goto(`/w/${workspace.slug}/views/${view.id}`);
  const sourceQuickAdd = page.getByTestId(`quick-add-${source.id}`);
  await sourceQuickAdd.fill('Quick-created in source label');
  const quickCreateResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/views/${view.id}/issues`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await sourceQuickAdd.press('Enter');
  expect((await quickCreateResponse).status()).toBe(201);
  await expect(page.getByText('Quick-created in source label')).toBeVisible();

  const emerging = await createLabel(request, token, workspace.id, 'emerging-dynamic', '#12a594');
  const dynamicResponse = await request.post(`/api/v1/views/${view.id}/issues`, {
    headers: { ...auth, 'Idempotency-Key': `mes187-${Date.now().toString(36)}` },
    data: { title: 'Dynamic value appeared', group_key: emerging.id },
  });
  expect(dynamicResponse.status(), await dynamicResponse.text()).toBe(201);
  const dynamicIssue = await dataOf<IssueData>(dynamicResponse);
  const emergingColumn = page.getByTestId(`board-column-${emerging.id}`);
  await expect(emergingColumn).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await expect(emergingColumn).toContainText('emerging-dynamic');
  await expect(emergingColumn.getByTestId(`board-card-${dynamicIssue.id}`)).toBeVisible();

  const sourcePrimaryCard = page
    .getByTestId(`board-column-${source.id}`)
    .getByTestId(`board-card-${primaryIssue.id}`);
  await expect(sourcePrimaryCard.getByTestId('issue-label-dot')).toHaveCount(3);
  await expect(sourcePrimaryCard.getByTestId('issue-label-overflow')).toHaveText('+1');

  // Merge through the real management UI while the board remains open in a
  // second page. Realtime convergence replaces every source dot and column.
  const settingsPage = await page.context().newPage();
  await settingsPage.goto(`/w/${workspace.slug}/settings/labels`);
  await expect(settingsPage.getByTestId(`label-row-${source.name}`)).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  await settingsPage.getByTestId(`label-merge-${source.name}`).click();
  await expect(settingsPage.getByTestId('label-merge-source')).toContainText(source.name);
  await expect(settingsPage.getByTestId('label-merge-impact')).toContainText('2');
  await settingsPage.getByTestId('label-merge-target').selectOption(target.id);
  const mergeResponse = settingsPage.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/labels/${source.id}/merge`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await settingsPage.getByTestId('label-merge-confirm').click();
  expect((await mergeResponse).status()).toBe(200);
  await expect(settingsPage.getByTestId(`label-row-${source.name}`)).toHaveCount(0);
  await expect(page.getByTestId(`board-column-${source.id}`)).toHaveCount(0, {
    timeout: RESPONSE_TIMEOUT,
  });
  const targetColumn = page.getByTestId(`board-column-${target.id}`);
  await expect(targetColumn.getByTestId(`board-card-${primaryIssue.id}`)).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  const mergedSummary = targetColumn
    .getByTestId(`board-card-${primaryIssue.id}`)
    .getByTestId('issue-label-summary');
  await expect(mergedSummary).toHaveAttribute('aria-label', new RegExp(target.name));
  await expect(mergedSummary).not.toHaveAttribute('aria-label', new RegExp(source.name));
  await expect(mergedSummary.getByTestId('issue-label-overflow')).toHaveText('+1');
  await settingsPage.close();

  // Strict next-state options and required-field failures stay inline. Agent
  // assignment exposes the notice; selecting the already persisted assignee
  // does not issue a second PATCH.
  await page.goto(`/w/${workspace.slug}/issues/${stateIssue.id}`);
  const propertiesTrigger = page.getByTestId('detail-aside-trigger');
  await expect(propertiesTrigger).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await propertiesTrigger.click();
  const propertiesPanel = page.getByTestId('detail-aside-sheet');
  await expect(propertiesPanel).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  const statusSelect = propertiesPanel.getByTestId('issue-detail-status');
  await expect(statusSelect).toHaveValue(statuses.todo.id, { timeout: RESPONSE_TIMEOUT });
  await expect(propertiesPanel.getByTestId('issue-status-strict-hint')).toBeVisible();
  await expect(statusSelect.locator(`option[value="${statuses.inProgress.id}"]`)).toBeDisabled();
  await expect(statusSelect.locator(`option[value="${statuses.done.id}"]`)).toBeEnabled();
  const requiredResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === `/api/v1/issues/${stateIssue.id}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await statusSelect.selectOption(statuses.done.id);
  expect((await requiredResponse).status()).toBe(422);
  await expect(propertiesPanel.getByTestId('issue-status-validation-error')).toContainText(
    /Missing required fields: Release note/,
  );
  await expect(statusSelect).toHaveValue(statuses.todo.id);

  let assigneePatchCount = 0;
  const countAssigneePatch = (requestObject: {
    method(): string;
    url(): string;
    postData(): string | null;
  }) => {
    if (
      requestObject.method() === 'PATCH' &&
      new URL(requestObject.url()).pathname === `/api/v1/issues/${stateIssue.id}` &&
      (requestObject.postData() ?? '').includes('assignee_id')
    ) {
      assigneePatchCount += 1;
    }
  };
  page.on('request', countAssigneePatch);
  const assigneeSelect = propertiesPanel.getByTestId('issue-detail-assignee');
  const assignResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === `/api/v1/issues/${stateIssue.id}` &&
      (response.request().postData() ?? '').includes('assignee_id'),
    { timeout: RESPONSE_TIMEOUT },
  );
  await assigneeSelect.selectOption(agent.member.id);
  expect((await assignResponse).status()).toBe(200);
  await expect(propertiesPanel.getByTestId('issue-agent-assignee-hint')).toContainText(
    'Work will start automatically after saving',
  );
  expect(assigneePatchCount).toBe(1);
  await assigneeSelect.selectOption(agent.member.id);
  await page.waitForTimeout(400);
  expect(assigneePatchCount).toBe(1);
  page.off('request', countAssigneePatch);

  // Agent roster identity, disabled owner role, live run-state surface, and the
  // actual member/agent/issue detail sources are presented in one drawer.
  await page.goto(`/w/${workspace.slug}/members`);
  const roleSelect = page.getByTestId(`role-select-${agent.member.id}`);
  await expect(roleSelect.locator('option[value="owner"]')).toBeDisabled();
  await expect(page.getByTestId(`ai-badge-${agent.member.id}`)).toBeVisible();
  await expect(page.getByTestId(`member-presence-${agent.member.id}`)).toBeVisible();
  await page
    .getByTestId(`member-open-${agent.member.id}`)
    .locator('.mesh-members__subtext')
    .hover();
  await expect(page.getByRole('tooltip')).toContainText('Can verify releases');
  await openMemberDrawer(page, agent.member.id);
  const drawer = page.getByTestId('member-drawer');
  await expect(drawer).toContainText('Strict transition');
  await expect(drawer).toContainText('Recently updated assigned issues');
  await expect(page.getByTestId('member-detail-runtime')).toBeVisible();
  await expect(drawer.getByRole('link', { name: /Open agent settings/ })).toHaveAttribute(
    'href',
    `/w/${workspace.slug}/agents/${agent.id}`,
  );
  const drawerClose = page.locator('.mesh-drawer__close');
  await drawerClose.click();
  await expect(drawer).toHaveCount(0);

  // A real stale If-Match returns 409. The form rolls back to the freshly read
  // server state and discloses that recovery before project deletion.
  await page.goto(`/w/${workspace.slug}/projects/${project.id}/settings`);
  await expect(page.getByTestId('settings-name')).toHaveValue(project.name, {
    timeout: RESPONSE_TIMEOUT,
  });
  const currentProject = await api<ProjectData>(
    request,
    token,
    'GET',
    `/api/v1/projects/${project.id}`,
  );
  const externalName = `${project.name} external`;
  await api<ProjectData>(
    request,
    token,
    'PATCH',
    `/api/v1/projects/${project.id}`,
    { name: externalName },
    { 'If-Match': currentProject.updated_at },
  );
  await page.getByTestId('settings-name').fill(`${project.name} local stale`);
  const conflictResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === `/api/v1/projects/${project.id}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('settings-save').click();
  expect((await conflictResponse).status()).toBe(409);
  await expect(page.getByTestId('settings-name')).toHaveValue(externalName);
  await expect(page.locator('.mesh-toast--warn')).toContainText(/rolled back/);

  await page.getByTestId('settings-delete').click();
  await expect(page.getByTestId('settings-delete-confirm-text')).toContainText(project.key);
  await expect(page.getByTestId('settings-delete-confirm-text')).toContainText(/reserved forever/);
  const deleteResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'DELETE' &&
      new URL(response.url()).pathname === `/api/v1/projects/${project.id}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('settings-delete-confirm').click();
  expect((await deleteResponse).status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`/w/${workspace.slug}/projects$`));
  await page.getByTestId('new-project-button').click();
  await page.getByTestId('create-project-name').fill('Cannot reuse deleted key');
  await page.getByTestId('create-project-key').fill(project.key);
  await expect(page.getByText('This key is already reserved.')).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });
  await expect(page.getByTestId('create-project-submit')).toBeDisabled();

  // Profile clearing is an explicit null write; malformed HTTPS values are
  // rejected in the browser and independently by the authoritative API.
  await api<UserData>(request, token, 'PATCH', '/api/v1/users/me', {
    avatar_url: 'https://example.com/mesh-avatar.png',
  });
  await page.goto('/settings/profile');
  const avatarInput = page.getByLabel('Avatar URL');
  await expect(avatarInput).toHaveValue('https://example.com/mesh-avatar.png', {
    timeout: RESPONSE_TIMEOUT,
  });
  const clearResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === '/api/v1/users/me' &&
      (response.request().postData() ?? '').includes('avatar_url'),
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByRole('button', { name: 'Restore default avatar' }).click();
  expect((await clearResponse).status()).toBe(200);
  await expect(avatarInput).toHaveValue('');
  expect((await api<MeData>(request, token, 'GET', '/api/v1/users/me')).user.avatar_url).toBeNull();
  await avatarInput.fill('https:///missing-host');
  await avatarInput.blur();
  await expect(page.getByText('Use an HTTPS avatar URL.')).toBeVisible();
  const malformedServerResponse = await request.patch('/api/v1/users/me', {
    headers: auth,
    data: { avatar_url: 'https://' },
  });
  expect(malformedServerResponse.status()).toBe(400);
  const malformedServerError = (await malformedServerResponse.json()) as {
    error: { code: string; details: { avatar_url: string } };
  };
  expect(malformedServerError.error.code).toBe('validation_error');
  expect(malformedServerError.error.details.avatar_url).toBe('https://');

  const database = psqlJson<{
    source_labels: number;
    target_links: number;
    dynamic_issues: number;
    missing_required_values: number;
    deleted_projects: number;
    reserved_prefixes: number;
    avatar_cleared: boolean;
  }>(`
    SELECT json_build_object(
      'source_labels', (
        SELECT count(*) FROM labels WHERE id = ${sqlLiteral(source.id)}::uuid
      ),
      'target_links', (
        SELECT count(*) FROM issue_labels
        WHERE label_id = ${sqlLiteral(target.id)}::uuid
          AND workspace_id = ${sqlLiteral(workspace.id)}::uuid
      ),
      'dynamic_issues', (
        SELECT count(*) FROM issues
        WHERE workspace_id = ${sqlLiteral(workspace.id)}::uuid
          AND title = 'Dynamic value appeared'
          AND deleted_at IS NULL
      ),
      'missing_required_values', (
        SELECT count(*) FROM issue_custom_field_values
        WHERE issue_id = ${sqlLiteral(stateIssue.id)}::uuid
      ),
      'deleted_projects', (
        SELECT count(*) FROM projects
        WHERE id = ${sqlLiteral(project.id)}::uuid AND deleted_at IS NOT NULL
      ),
      'reserved_prefixes', (
        SELECT count(*) FROM identifier_prefix_registry
        WHERE workspace_id = ${sqlLiteral(workspace.id)}::uuid AND key = ${sqlLiteral(project.key)}
      ),
      'avatar_cleared', (
        SELECT avatar_url IS NULL FROM users WHERE id = ${sqlLiteral(data.user.id)}::uuid
      )
    );
  `);
  expect(database).toEqual({
    source_labels: 0,
    target_links: 2,
    dynamic_issues: 1,
    missing_required_values: 0,
    deleted_projects: 1,
    reserved_prefixes: 1,
    avatar_cleared: true,
  });
  await testInfo.attach('mes187-postgres-evidence', {
    body: Buffer.from(`${JSON.stringify(database, null, 2)}\n`),
    contentType: 'application/json',
  });
});
