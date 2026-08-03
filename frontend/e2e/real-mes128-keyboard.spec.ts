/**
 * MES-128 真栈窄屏键盘旅程。
 *
 * 不安装 route/mock handler：所有读写均经 Compose nginx → FastAPI → PostgreSQL，
 * 实时链路由 gateway/worker 承担。每个视口完整执行：
 * 登录 → `c` 建 issue → `g b` 进看板并用方向键移动 → Ctrl+Enter 评论 →
 * 键盘切换工作区 → `/` 搜索，并逐步断言 HTTP 响应与数据库真值。
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page, TestInfo } from '@playwright/test';

const PG_CONTAINER = process.env.MES128_PG_CONTAINER ?? 'mes128-real-postgres-1';
const RESPONSE_TIMEOUT = 30_000;
const EVIDENCE_DIR =
  process.env.MES128_EVIDENCE_DIR ??
  join(dirname(fileURLToPath(import.meta.url)), 'evidence', 'mes111-b5-real');
const EVIDENCE_MANIFEST = join(EVIDENCE_DIR, 'manifest.json');

interface ApiResponseEvidence {
  readonly method: string;
  readonly status: number;
  readonly path: string;
}

interface ScreenshotEvidence {
  readonly file: string;
  readonly sha256: string;
  readonly width: number;
  readonly height: number;
}

interface Envelope<T> {
  readonly data: T;
}

interface WorkspaceData {
  readonly id: string;
  readonly slug: string;
}

interface IssueData {
  readonly id: string;
  readonly identifier: string;
  readonly workspace_id: string;
  readonly title: string;
  readonly state_category: string;
  readonly version: number;
}

interface DbIssue {
  readonly id: string;
  readonly workspace_id: string;
  readonly title: string;
  readonly state_category: string;
  readonly version: number;
}

interface DbEvidence {
  readonly user_count: number;
  readonly session_count: number;
  readonly membership_count: number;
  readonly autopilot_enabled: boolean;
  readonly first_issue: DbIssue;
  readonly comment_count: number;
  readonly second_issue_count: number;
}

interface ProjectEvidence {
  readonly viewport: { readonly width: number; readonly height: number };
  readonly journey: readonly string[];
  readonly api_responses: readonly ApiResponseEvidence[];
  readonly database: DbEvidence;
  readonly database_assertions: Readonly<Record<string, string>>;
  readonly screenshots: readonly ScreenshotEvidence[];
}

interface EvidenceManifest {
  readonly schema_version: 1;
  readonly generated_at: string;
  readonly stack: {
    readonly auth_mode: 'production';
    readonly browser_entrypoint: 'frontend-loopback-only';
    readonly mocked_routes: 0;
    readonly database: 'PostgreSQL';
    readonly host_published_ports: readonly ['127.0.0.1:18430->frontend:80/tcp'];
    readonly private_services: readonly ['PostgreSQL', 'Redis', 'MinIO', 'API', 'gateway'];
  };
  readonly projects: Readonly<Record<string, ProjectEvidence>>;
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

/** APIRequestContext 与浏览器 page.waitForResponse 的响应都满足此最小 JSON 契约。 */
async function dataOf<T>(response: { json(): Promise<unknown> }): Promise<T> {
  return ((await response.json()) as Envelope<T>).data;
}

async function postData<T>(
  request: APIRequestContext,
  path: string,
  token: string,
  data: Record<string, unknown>,
  evidence: ApiResponseEvidence[],
  expectedStatus = 201,
): Promise<T> {
  const response = await request.post(path, {
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  evidence.push({ method: 'POST', status: response.status(), path });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(expectedStatus);
  return dataOf<T>(response);
}

async function typeWithKeyboard(page: Page, locator: Locator, value: string): Promise<void> {
  await locator.focus();
  await expect(locator).toBeFocused();
  await page.keyboard.type(value);
  await expect(locator).toHaveValue(value);
}

async function screenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<ScreenshotEvidence> {
  const body = await page.screenshot({ fullPage: true });
  const file = `${testInfo.project.name}-${name}.png`;
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await writeFile(join(EVIDENCE_DIR, file), body);
  await testInfo.attach(name, { body, contentType: 'image/png' });
  return {
    file,
    sha256: createHash('sha256').update(body).digest('hex'),
    // PNG IHDR stores unsigned big-endian width/height at byte offsets 16/20.
    width: body.readUInt32BE(16),
    height: body.readUInt32BE(20),
  };
}

async function persistEvidence(
  project: string,
  evidence: ProjectEvidence,
  forbiddenValues: readonly string[],
): Promise<void> {
  await mkdir(EVIDENCE_DIR, { recursive: true });
  let projects: Readonly<Record<string, ProjectEvidence>> = {};
  // Full runs start with desktop-1440; reset stale project metadata while fixed-name
  // screenshots are overwritten. Later/single-project reruns preserve earlier projects.
  if (project !== 'desktop-1440') {
    try {
      const prior = JSON.parse(await readFile(EVIDENCE_MANIFEST, 'utf8')) as EvidenceManifest;
      projects = prior.projects;
    } catch {
      projects = {};
    }
  }
  const manifest: EvidenceManifest = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    stack: {
      auth_mode: 'production',
      browser_entrypoint: 'frontend-loopback-only',
      mocked_routes: 0,
      database: 'PostgreSQL',
      host_published_ports: ['127.0.0.1:18430->frontend:80/tcp'],
      private_services: ['PostgreSQL', 'Redis', 'MinIO', 'API', 'gateway'],
    },
    projects: { ...projects, [project]: evidence },
  };
  const body = `${JSON.stringify(manifest, null, 2)}\n`;
  for (const forbidden of forbiddenValues) {
    expect(body).not.toContain(forbidden);
  }
  await writeFile(EVIDENCE_MANIFEST, body, 'utf8');
}

function dbEvidence(
  email: string,
  firstWorkspaceId: string,
  firstIssueId: string,
  commentBody: string,
  secondWorkspaceId: string,
  secondIssueId: string,
): DbEvidence {
  return psqlJson<DbEvidence>(`
    SELECT json_build_object(
      'user_count', (
        SELECT count(*) FROM users WHERE email = ${sqlLiteral(email)}
      ),
      'session_count', (
        SELECT count(*) FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE u.email = ${sqlLiteral(email)} AND s.revoked_at IS NULL
      ),
      'membership_count', (
        SELECT count(*) FROM members m
        JOIN users u ON u.id = m.user_id
        WHERE u.email = ${sqlLiteral(email)}
          AND m.workspace_id IN (${sqlLiteral(firstWorkspaceId)}::uuid, ${sqlLiteral(secondWorkspaceId)}::uuid)
          AND m.status = 'active'
      ),
      'autopilot_enabled', (
        SELECT (w.settings->'feature_flags'->>'autopilot')::boolean
        FROM workspaces w WHERE w.id = ${sqlLiteral(firstWorkspaceId)}::uuid
      ),
      'first_issue', (
        SELECT json_build_object(
          'id', i.id,
          'workspace_id', i.workspace_id,
          'title', i.title,
          'state_category', i.state_category,
          'version', i.version
        )
        FROM issues i WHERE i.id = ${sqlLiteral(firstIssueId)}::uuid
      ),
      'comment_count', (
        SELECT count(*) FROM comments c
        WHERE c.issue_id = ${sqlLiteral(firstIssueId)}::uuid
          AND c.body_markdown = ${sqlLiteral(commentBody)}
          AND c.deleted_at IS NULL
      ),
      'second_issue_count', (
        SELECT count(*) FROM issues i
        WHERE i.id = ${sqlLiteral(secondIssueId)}::uuid
          AND i.workspace_id = ${sqlLiteral(secondWorkspaceId)}::uuid
          AND i.deleted_at IS NULL
      )
    );
  `);
}

test.describe.configure({ mode: 'serial' });

test('登录→建 issue→键盘移卡→评论→切工作区→搜索，API 与 PostgreSQL 一致', async ({
  page,
  request,
}, testInfo) => {
  const viewport = testInfo.project.name;
  const suffix = `${viewport.replaceAll('-', '')}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const email = `mes128-${suffix}@example.com`;
  const password = `Mesh#${suffix}A9!`;
  const firstSlug = `m128a-${suffix}`.slice(0, 48);
  const secondSlug = `m128b-${suffix}`.slice(0, 48);
  const firstIssueTitle = `Keyboard issue ${suffix}`;
  const secondIssueTitle = `Search target ${suffix}`;
  const commentBody = `Keyboard comment ${suffix}`;
  const apiResponses: ApiResponseEvidence[] = [];
  const screenshots: ScreenshotEvidence[] = [];

  page.on('response', (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/v1/')) {
      apiResponses.push({
        method: response.request().method(),
        status: response.status(),
        path: `${url.pathname}${url.search}`,
      });
    }
  });

  // 账号仅作旅程前置数据；登录本身必须由真实页面键盘提交。
  const registration = await request.post('/api/v1/auth/register', {
    data: { email, password, display_name: `MES-128 ${viewport}` },
  });
  apiResponses.push({
    method: 'POST',
    status: registration.status(),
    path: '/api/v1/auth/register',
  });
  expect([200, 201]).toContain(registration.status());

  await page.goto('/login');
  await typeWithKeyboard(page, page.getByTestId('login-email'), email);
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('login-password')).toBeFocused();
  await page.keyboard.type(password);
  const loginResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/auth/login',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.keyboard.press('Enter');
  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);
  const loginData = await dataOf<{ access_token: string }>(loginResponse);
  expect(loginData.access_token).toMatch(/^eyJ/);
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });

  // MES-158:真实 production shell 必须由 Appica 子路径组件渲染，同时仍由
  // Mesh 的 data-theme 协商链掌权。惰性 script 证明组件库未引入第二条首帧链路。
  await expect(page.locator('html')).toHaveAttribute('data-theme', /^(light|dark)$/);
  await expect(page.locator('html')).toHaveClass(/\b(light|dark)\b/);
  await expect(page.locator('.mesh-sidebar')).toHaveAttribute('data-slot', 'navigation');
  await expect(page.getByTestId('topbar-search')).toHaveAttribute('data-slot', 'input');
  await expect(page.locator('script[data-mesh-theme-bridge]')).toHaveAttribute(
    'type',
    'application/json',
  );
  screenshots.push(await screenshot(page, testInfo, '01-login'));

  // 通过真实设置页与 PATCH /users/me 切换暗色，再回到亮色。除 Mesh 权威
  // data-theme 外，Appica dark variant 的兼容 class 必须同帧同步且互斥。
  await page.goto('/settings/appearance');
  const darkPreferenceResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === '/api/v1/users/me',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('theme-select').selectOption('dark');
  expect((await darkPreferenceResponse).status()).toBe(200);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('html')).toHaveClass(/\bdark\b/);
  await expect(page.locator('html')).not.toHaveClass(/\blight\b/);
  screenshots.push(await screenshot(page, testInfo, '01b-dark-shell'));

  const lightPreferenceResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === '/api/v1/users/me',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('theme-select').selectOption('light');
  expect((await lightPreferenceResponse).status()).toBe(200);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveClass(/\blight\b/);
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/);
  await page.goto('/');

  const token = loginData.access_token;
  const firstWorkspace = await postData<WorkspaceData>(
    request,
    '/api/v1/workspaces',
    token,
    { name: `Keyboard A ${suffix}`, slug: firstSlug },
    apiResponses,
  );
  const secondWorkspace = await postData<WorkspaceData>(
    request,
    '/api/v1/workspaces',
    token,
    { name: `Keyboard B ${suffix}`, slug: secondSlug },
    apiResponses,
  );

  // G15 工作区开关必须经过真实 PATCH 类型校验并持久化；非法字符串不能污染数据库。
  const invalidFlag = await request.patch(`/api/v1/workspaces/${firstWorkspace.id}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { settings: { feature_flags: { autopilot: 'off' } } },
  });
  apiResponses.push({
    method: 'PATCH',
    status: invalidFlag.status(),
    path: `/api/v1/workspaces/${firstWorkspace.id}`,
  });
  expect(invalidFlag.status(), await invalidFlag.text()).toBe(400);
  expect((await invalidFlag.json()) as unknown).toMatchObject({
    error: { code: 'validation_error' },
  });

  const disabledFlag = await request.patch(`/api/v1/workspaces/${firstWorkspace.id}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { settings: { feature_flags: { autopilot: false } } },
  });
  apiResponses.push({
    method: 'PATCH',
    status: disabledFlag.status(),
    path: `/api/v1/workspaces/${firstWorkspace.id}`,
  });
  expect(disabledFlag.status(), await disabledFlag.text()).toBe(200);
  expect(
    (await dataOf<{ settings: { feature_flags: { autopilot: boolean } } }>(disabledFlag)).settings,
  ).toMatchObject({ feature_flags: { autopilot: false } });

  const view = await postData<{ id: string }>(
    request,
    `/api/v1/workspaces/${firstWorkspace.id}/views`,
    token,
    {
      name: `Keyboard board ${suffix}`,
      layout: 'board',
      visibility: 'shared',
      group_by: 'state_category',
      is_default: true,
    },
    apiResponses,
  );
  const secondIssue = await postData<IssueData>(
    request,
    `/api/v1/workspaces/${secondWorkspace.id}/issues`,
    token,
    { title: secondIssueTitle },
    apiResponses,
  );

  // `c` 是产品快捷键；标题与提交均由键盘完成，并核对真实 201 响应。
  await page.reload();
  await expect(page.getByTestId(`home-workspace-${firstSlug}`)).toBeVisible({ timeout: 30_000 });
  const issuePageLoadedPromise = Promise.all([
    page.waitForResponse(
      (response) => {
        const url = new URL(response.url());
        return (
          response.request().method() === 'GET' &&
          url.pathname === `/api/v1/workspaces/${firstWorkspace.id}/issues` &&
          url.searchParams.get('limit') === '25'
        );
      },
      { timeout: RESPONSE_TIMEOUT },
    ),
    page.waitForResponse(
      (response) =>
        response.request().method() === 'GET' &&
        new URL(response.url()).pathname === `/api/v1/workspaces/${firstWorkspace.id}/statuses`,
      { timeout: RESPONSE_TIMEOUT },
    ),
  ]);
  await page.getByTestId('topbar-brand').focus();
  await page.keyboard.press('c');
  // 新 main 的规范深链契约在多工作区且无 active workspace 时先进入
  // workspace picker。继续用键盘选择首个工作区，保留 ?create=1 意图。
  await expect(page).toHaveURL(/\/workspace-picker\?next=/);
  const firstWorkspaceChoice = page.getByTestId(`ws-picker-${firstSlug}`);
  await expect(firstWorkspaceChoice).toBeVisible();
  await firstWorkspaceChoice.focus();
  await expect(firstWorkspaceChoice).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`/w/${firstSlug}/issues\\?create=1`));
  await issuePageLoadedPromise;
  await expect(page.getByTestId('issues-skeleton')).toBeHidden();
  const createTitle = page.getByTestId('issue-create-title');
  await expect(createTitle).toHaveAttribute('data-slot', 'input');
  await typeWithKeyboard(page, createTitle, firstIssueTitle);
  const createResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/workspaces/${firstWorkspace.id}/issues`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.keyboard.press('Enter');
  const createResponse = await createResponsePromise;
  expect(createResponse.status()).toBe(201);
  const firstIssue = await dataOf<IssueData>(createResponse);
  expect(firstIssue).toMatchObject({
    workspace_id: firstWorkspace.id,
    title: firstIssueTitle,
    state_category: 'todo',
  });
  await expect(page.getByTestId(`issue-row-${firstIssue.identifier}`)).toContainText(
    firstIssueTitle,
  );
  await expect(page.getByTestId('issue-table')).toHaveAttribute('data-slot', 'table');
  screenshots.push(await screenshot(page, testInfo, '02-issue-created'));

  // `g b` 打开默认看板；方向键进入移动模式并切列，Enter 提交，未触发拖拽。
  await page.getByTestId('topbar-brand').focus();
  await page.keyboard.press('g');
  await page.keyboard.press('b');
  await expect(page).toHaveURL(/\/board$/);
  const compactBoard = testInfo.project.name.startsWith('phone-');
  if (compactBoard) {
    // 窄屏看板默认展示第一列(backlog)，先用键盘切到 issue 所在的 todo 列。
    const todoChip = page.getByTestId('compact-chip-todo');
    await expect(todoChip).toBeVisible({ timeout: 30_000 });
    await todoChip.focus();
    await page.keyboard.press('Enter');
    await expect(todoChip).toHaveAttribute('aria-selected', 'true');
  }
  await expect(page.getByTestId(`board-card-${firstIssue.id}`)).toBeVisible({ timeout: 30_000 });
  const card = page.getByTestId(`board-card-${firstIssue.id}`);
  let pointerDownCount = 0;
  let dragStartCount = 0;
  await card.evaluate((element) => {
    element.addEventListener('pointerdown', () => {
      (window as typeof window & { __mes128PointerDown?: number }).__mes128PointerDown =
        ((window as typeof window & { __mes128PointerDown?: number }).__mes128PointerDown ?? 0) + 1;
    });
    element.addEventListener('dragstart', () => {
      (window as typeof window & { __mes128DragStart?: number }).__mes128DragStart =
        ((window as typeof window & { __mes128DragStart?: number }).__mes128DragStart ?? 0) + 1;
    });
  });
  await card.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('board-live')).toContainText(/Move mode|移动模式/);
  await page.keyboard.press('ArrowRight');
  const moveResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/views/${view.id}/moves`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.keyboard.press('Enter');
  const moveResponse = await moveResponsePromise;
  expect(moveResponse.status()).toBe(200);
  const movedIssue = await dataOf<IssueData>(moveResponse);
  expect(movedIssue).toMatchObject({ id: firstIssue.id, state_category: 'in_progress' });
  expect(movedIssue.version).toBe(firstIssue.version + 1);
  ({ pointerDownCount, dragStartCount } = await page.evaluate(() => ({
    pointerDownCount:
      (window as typeof window & { __mes128PointerDown?: number }).__mes128PointerDown ?? 0,
    dragStartCount:
      (window as typeof window & { __mes128DragStart?: number }).__mes128DragStart ?? 0,
  })));
  expect({ pointerDownCount, dragStartCount }).toEqual({ pointerDownCount: 0, dragStartCount: 0 });
  if (compactBoard) {
    await page.getByTestId('compact-chip-in_progress').focus();
    await page.keyboard.press('Enter');
  }
  await expect(
    page.getByTestId('board-column-in_progress').getByTestId(`board-card-${firstIssue.id}`),
  ).toBeVisible({ timeout: 20_000 });
  screenshots.push(await screenshot(page, testInfo, '03-keyboard-moved'));

  // 评论输入与提交均走键盘，Ctrl+Enter 命中真实 comments 写端点。
  await page.goto(`/issues/${firstIssue.id}`);
  await expect(page.getByTestId('issue-detail')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('issue-detail-title')).toHaveAttribute('data-slot', 'input');
  await expect(page.getByTestId('issue-detail-description')).toHaveAttribute(
    'data-slot',
    'textarea',
  );
  await expect(page.getByTestId('attachment-composer')).toBeVisible();
  await expect(page.getByTestId('attachments-empty')).toBeVisible();
  const issueTabs = page.locator('[data-slot="tabs-trigger"]');
  await expect(issueTabs).toHaveCount(2);
  await issueTabs.nth(1).click();
  await expect(page.getByTestId('issue-detail-activity')).toBeVisible();
  await issueTabs.nth(0).click();
  const composer = page.getByTestId('composer-input');
  await expect(composer).toHaveAttribute('data-slot', 'textarea');
  await typeWithKeyboard(page, composer, commentBody);
  const commentResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/issues/${firstIssue.id}/comments`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.keyboard.press('Control+Enter');
  const commentResponse = await commentResponsePromise;
  expect(commentResponse.status()).toBe(201);
  const comment = await dataOf<{ id: string; body_markdown: string }>(commentResponse);
  expect(comment.body_markdown).toBe(commentBody);
  await expect(page.locator(`[data-testid="comment-card-${comment.id}"]`)).toContainText(
    commentBody,
  );
  screenshots.push(await screenshot(page, testInfo, '04-comment'));

  // 工作区切换器只用 Enter 激活；同时核对 by-slug 的真实工作区响应。
  await page.getByTestId('ws-switcher-button').focus();
  await page.keyboard.press('Enter');
  const secondWorkspaceButton = page.getByTestId(`ws-switcher-item-${secondSlug}`);
  await expect(secondWorkspaceButton).toBeVisible({ timeout: 20_000 });
  await secondWorkspaceButton.focus();
  const switchResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === `/api/v1/workspaces/by-slug/${secondSlug}`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.keyboard.press('Enter');
  const switchResponse = await switchResponsePromise;
  expect(switchResponse.status()).toBe(200);
  const switchedWorkspace = await dataOf<WorkspaceData>(switchResponse);
  expect(switchedWorkspace.id).toBe(secondWorkspace.id);
  await expect(page).toHaveURL(new RegExp(`/w/${secondSlug}$`));
  await expect(page.getByTestId('ws-home-name')).toContainText(`Keyboard B ${suffix}`);
  screenshots.push(await screenshot(page, testInfo, '05-workspace-switched'));

  // `/` 聚焦真实全局搜索；等待工作区作用域 search 响应后用 Enter 打开结果。
  await page.keyboard.press('/');
  const searchInput = page.getByTestId('topbar-search');
  await expect(searchInput).toBeFocused();
  const searchResponsePromise = page.waitForResponse(
    (response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === 'GET' &&
        url.pathname === `/api/v1/workspaces/${secondWorkspace.id}/search` &&
        url.searchParams.get('q') === secondIssueTitle
      );
    },
    { timeout: RESPONSE_TIMEOUT },
  );
  // 首字符触发顶栏 → 命令面板的异步焦点交接；确认交接完成后再继续键入，
  // 避免真实浏览器把后续字符仍投递给正在卸载的顶栏控件。
  await page.keyboard.type(secondIssueTitle.slice(0, 1));
  const paletteInput = page.getByRole('dialog').getByRole('combobox');
  await expect(paletteInput).toBeFocused();
  await expect(paletteInput).toHaveValue(secondIssueTitle.slice(0, 1));
  await page.keyboard.type(secondIssueTitle.slice(1));
  const searchResponse = await searchResponsePromise;
  expect(searchResponse.status()).toBe(200);
  const searchData =
    await dataOf<Array<{ id: string; type: string; title: string }>>(searchResponse);
  expect(searchData).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: secondIssue.id, type: 'issue', title: secondIssueTitle }),
    ]),
  );
  await expect(page.getByTestId(`palette-opt-issue:${secondIssue.id}`)).toBeVisible({
    timeout: 20_000,
  });
  // 顶栏搜索的键盘契约要求先用方向键显式选择结果，再由 Enter 激活。
  await page.keyboard.press('ArrowDown');
  await expect(paletteInput).toHaveAttribute(
    'aria-activedescendant',
    `palette-opt-issue:${secondIssue.id}`,
  );
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`/issues/${secondIssue.id}$`), { timeout: 30_000 });
  await expect(page.getByTestId('issue-detail-title')).toHaveValue(secondIssueTitle);
  screenshots.push(await screenshot(page, testInfo, '06-search-opened'));

  const database = dbEvidence(
    email,
    firstWorkspace.id,
    firstIssue.id,
    commentBody,
    secondWorkspace.id,
    secondIssue.id,
  );
  expect(database).toMatchObject({
    user_count: 1,
    membership_count: 2,
    autopilot_enabled: false,
    comment_count: 1,
    second_issue_count: 1,
    first_issue: {
      id: firstIssue.id,
      workspace_id: firstWorkspace.id,
      title: firstIssueTitle,
      state_category: 'in_progress',
      version: firstIssue.version + 1,
    },
  });
  // 注册前置会话在随后登录时被轮换；数据库应只保留一个未撤销会话。
  expect(database.session_count).toBe(1);
  expect(screenshots).toHaveLength(7);

  const viewportSize = page.viewportSize();
  if (viewportSize === null) throw new Error('MES-128 evidence requires an explicit viewport');
  const projectEvidence: ProjectEvidence = {
    viewport: viewportSize,
    journey: [
      'keyboard login',
      'real settings light/dark bridge round-trip',
      'keyboard issue create',
      'arrow-key non-drag board move',
      'Ctrl+Enter comment',
      'keyboard workspace switch',
      'slash search, ArrowDown selection, Enter activation',
    ],
    api_responses: apiResponses,
    database,
    database_assertions: {
      user_count: 'equals 1',
      active_session_count: 'equals 1 after login rotation',
      active_membership_count: 'equals 2 across the two workspaces',
      first_workspace_autopilot_flag: 'equals false after rejecting a non-boolean value',
      first_issue_state: 'equals in_progress with version incremented by 1',
      exact_comment_count: 'equals 1 for the submitted body',
      second_workspace_search_issue: 'exists exactly once in the second workspace',
    },
    screenshots,
  };
  await persistEvidence(viewport, projectEvidence, [email, password, token]);

  await testInfo.attach('real-api-and-postgresql-evidence', {
    body: Buffer.from(JSON.stringify(projectEvidence, null, 2)),
    contentType: 'application/json',
  });
});
