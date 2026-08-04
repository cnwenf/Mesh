/**
 * MES-159 项目页 production 真栈旅程。
 *
 * 浏览器只访问同源 nginx；注册、登录、工作区首页、项目列表、项目创建、
 * 项目详情、健康度更新和页签均由真实 UI 操作，最终直查容器内 PostgreSQL。
 */
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const PG_CONTAINER = process.env.MES128_PG_CONTAINER ?? 'mes128-real-postgres-1';
const RESPONSE_TIMEOUT = 30_000;
const EVIDENCE_DIR =
  process.env.MES128_EVIDENCE_DIR ??
  join(dirname(fileURLToPath(import.meta.url)), 'evidence', 'mes111-b5-real');

interface Envelope<T> {
  readonly data: T;
}

interface WorkspaceData {
  readonly id: string;
  readonly slug: string;
}

interface ProjectData {
  readonly id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly key: string;
}

interface ProjectDatabaseEvidence {
  readonly project_count: number;
  readonly health: string;
  readonly update_count: number;
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

test('工作区首页→项目创建→详情状态与页签，API 和 PostgreSQL 一致', async ({
  page,
  request,
}, testInfo) => {
  const viewport = testInfo.project.name.replaceAll('-', '');
  const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const email = `mes159-${viewport}-${nonce}@example.com`;
  const password = `Mesh#${nonce}A9!`;
  const workspaceSlug = `m159-${viewport}-${nonce}`.slice(0, 48);
  const projectName = `Core workspace ${nonce}`;
  const projectKey = `M${Math.random().toString(36).slice(2, 7).toUpperCase()}`;

  const registration = await request.post('/api/v1/auth/register', {
    data: { email, password, display_name: `MES-159 ${testInfo.project.name}` },
  });
  expect([200, 201]).toContain(registration.status());

  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(password);
  const loginResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/auth/login',
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('login-account-submit').click();
  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);
  const token = (await dataOf<{ access_token: string }>(loginResponse)).access_token;
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });

  const workspaceResponse = await request.post('/api/v1/workspaces', {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `MES-159 ${nonce}`, slug: workspaceSlug },
  });
  expect(workspaceResponse.status(), await workspaceResponse.text()).toBe(201);
  const workspace = await dataOf<WorkspaceData>(workspaceResponse);

  await page.goto(`/w/${workspaceSlug}`);
  await expect(page.getByTestId('ws-home-name')).toContainText(`MES-159 ${nonce}`, {
    timeout: 30_000,
  });
  const projectsLink = page.getByTestId('ws-quick-projects');
  await expect(projectsLink).toHaveAttribute('href', `/w/${workspaceSlug}/projects`);
  await projectsLink.click();
  await expect(page).toHaveURL(new RegExp(`/w/${workspaceSlug}/projects$`));

  const createButton = page.getByTestId('new-project-button');
  await expect(createButton).toHaveAttribute('data-slot', 'button');
  await createButton.click();
  const nameInput = page.getByTestId('create-project-name');
  const keyInput = page.getByTestId('create-project-key');
  await expect(nameInput).toHaveAttribute('data-slot', 'input');
  await expect(keyInput).toHaveAttribute('data-slot', 'input');
  await expect(
    page.getByTestId('create-project-form').locator('[data-slot="textarea"]'),
  ).toHaveCount(1);
  await nameInput.fill(projectName);
  await keyInput.fill(projectKey);

  const createResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/workspaces/${workspace.id}/projects`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('create-project-submit').click();
  const createResponse = await createResponsePromise;
  expect(createResponse.status(), await createResponse.text()).toBe(201);
  const project = await dataOf<ProjectData>(createResponse);
  expect(project).toMatchObject({
    workspace_id: workspace.id,
    name: projectName,
    key: projectKey,
  });
  await expect(page).toHaveURL(new RegExp(`/w/${workspaceSlug}/projects/${project.id}$`), {
    timeout: 30_000,
  });
  await expect(page.getByTestId('project-detail-header')).toContainText(projectName);
  await expect(page.getByRole('tablist')).toHaveAttribute('data-slot', 'tabs-list');
  await expect(page.getByTestId('tab-overview')).toHaveAttribute('data-slot', 'tabs-trigger');

  const healthButton = page.getByTestId('health-light-button');
  await expect(healthButton).toHaveAttribute('data-slot', 'button');
  await healthButton.click();
  await page.getByTestId('health-select').selectOption('at_risk');
  const healthResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/projects/${project.id}/updates`,
    { timeout: RESPONSE_TIMEOUT },
  );
  await page.getByTestId('health-update-submit').click();
  const healthResponse = await healthResponsePromise;
  expect(healthResponse.status(), await healthResponse.text()).toBe(201);
  await expect(page.getByTestId('health-light-button')).toContainText(/At risk|有风险/, {
    timeout: 30_000,
  });

  await page.getByTestId('tab-milestones').click();
  await expect(page.getByTestId('tab-milestones')).toHaveAttribute('aria-selected', 'true');
  await page.getByTestId('tab-updates').click();
  await expect(page.getByTestId('tab-updates')).toHaveAttribute('aria-selected', 'true');

  const screenshot = await page.screenshot({ fullPage: true });
  const screenshotFile = `${testInfo.project.name}-mes159-project-detail.png`;
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await writeFile(join(EVIDENCE_DIR, screenshotFile), screenshot);
  await testInfo.attach('mes159-project-detail', {
    body: screenshot,
    contentType: 'image/png',
  });

  await page.goto(`/w/${workspaceSlug}/projects`);
  const projectCard = page.getByTestId(`project-card-${project.id}`);
  await expect(projectCard).toContainText(projectName, { timeout: 30_000 });
  await expect(projectCard.getByRole('link', { name: projectName })).toHaveAttribute(
    'href',
    `/w/${workspaceSlug}/projects/${project.id}`,
  );
  await page.getByTestId('projects-status-filter').selectOption('planning');
  await expect(page).toHaveURL(/status=planning/);
  await expect(projectCard).toBeVisible();

  const database = psqlJson<ProjectDatabaseEvidence>(`
    SELECT json_build_object(
      'project_count', (
        SELECT count(*) FROM projects
        WHERE id = ${sqlLiteral(project.id)}::uuid
          AND workspace_id = ${sqlLiteral(workspace.id)}::uuid
          AND name = ${sqlLiteral(projectName)}
          AND key = ${sqlLiteral(projectKey)}
          AND deleted_at IS NULL
      ),
      'health', (
        SELECT health FROM projects WHERE id = ${sqlLiteral(project.id)}::uuid
      ),
      'update_count', (
        SELECT count(*) FROM project_updates
        WHERE project_id = ${sqlLiteral(project.id)}::uuid
          AND health = 'at_risk'
      )
    );
  `);
  expect(database).toEqual({
    project_count: 1,
    health: 'at_risk',
    update_count: 1,
  });

  const databaseFile = `${testInfo.project.name}-mes159-project-database.json`;
  const evidenceBody = `${JSON.stringify(
    {
      schema_version: 1,
      viewport: testInfo.project.name,
      project: {
        id: project.id,
        workspace_id: workspace.id,
        name: projectName,
        key: projectKey,
      },
      database,
    },
    null,
    2,
  )}\n`;
  await writeFile(join(EVIDENCE_DIR, databaseFile), evidenceBody, 'utf8');
  await testInfo.attach('mes159-project-database', {
    body: Buffer.from(evidenceBody),
    contentType: 'application/json',
  });
});
