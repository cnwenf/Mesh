/**
 * MES-161 全局辅助页族真实 e2e：独立注册入口、onboarding、统一命令面板、
 * Analytics，以及 403/404 恢复页。所有写入均走 production 鉴权的真实 HTTP
 * 服务与 PostgreSQL；全旅程覆盖桌面/手机，Analytics 与恢复页另做亮暗主题复验。
 *
 * 一键运行（仓库根目录）：
 *   MES128_COMPOSE_PROJECT=mes161 ./frontend/e2e/mes128-real/run-e2e.sh
 * 该 runner 生成强随机凭据，只发布 loopback 前端口，并在退出时回收容器与卷。
 */
import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const PASSWORD = 'Mesh-E2E#2026x';
const EVIDENCE_DIR = 'e2e/evidence/mes161';
const LOAD_FAILED_TEXT = 'We could not load this content. Please try again.';
const PG_CONTAINER = process.env.MES161_PG_CONTAINER ?? 'mes161-postgres-1';

interface DatabaseEvidence {
  readonly user_count: number;
  readonly workspace_count: number;
  readonly active_membership_count: number;
}

function sqlLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

function databaseEvidence(email: string, slug: string): DatabaseEvidence {
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
      `SELECT json_build_object(
        'user_count', (SELECT count(*) FROM users WHERE email = ${sqlLiteral(email)}),
        'workspace_count', (SELECT count(*) FROM workspaces WHERE slug = ${sqlLiteral(slug)}),
        'active_membership_count', (
          SELECT count(*) FROM members m
          JOIN users u ON u.id = m.user_id
          JOIN workspaces w ON w.id = m.workspace_id
          WHERE u.email = ${sqlLiteral(email)}
            AND w.slug = ${sqlLiteral(slug)}
            AND m.status = 'active'
        )
      );`,
    ],
    { encoding: 'utf8', timeout: 30_000 },
  ).trim();
  return JSON.parse(output) as DatabaseEvidence;
}

function uniqueEmail(project: string): string {
  return `mes161-${project}-${String(process.pid)}-${String(Date.now())}@example.com`;
}

async function evidence(page: Page, stem: string): Promise<void> {
  await page.screenshot({ path: `${EVIDENCE_DIR}/${test.info().project.name}-${stem}.png` });
}

async function register(page: Page): Promise<string> {
  // 直达注册入口，并用协议相对 next 验证开放重定向守卫最终安全回落首页。
  await page.goto('/register?next=//outside.example/path');
  await expect(page.getByTestId('login-mode-register')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('login-display-name')).toBeVisible();
  await evidence(page, 'register-light');

  const email = uniqueEmail(test.info().project.name);
  await page.getByTestId('login-display-name').fill(`MES-161 ${test.info().project.name}`);
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.getByTestId('register-verify-sent')).toContainText(email);
  await page.getByTestId('register-continue').click();
  await page.waitForURL((url) => new URL(url).pathname === '/');
  await expect(page.getByTestId('home-no-workspaces')).toBeVisible();
  return email;
}

async function createWorkspace(page: Page): Promise<string> {
  const slug = `mes161-${test.info().project.name}-${String(Date.now()).slice(-7)}`;
  await page.getByTestId('home-create-workspace').click();
  await page.getByTestId('ws-wizard-name-input').fill('MES-161 Workspace');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(slug);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.getByTestId('ws-wizard-skip').click();
  await page.goto('/');
  await expect(page.getByTestId(`home-workspace-${slug}`)).toBeVisible();
  return slug;
}

async function setTheme(page: Page, mode: 'light' | 'dark'): Promise<void> {
  await page.goto('/settings/appearance');
  await page.getByTestId('theme-select').selectOption(mode);
  await expect(page.locator('html')).toHaveAttribute('data-theme', mode);
}

test.describe('MES-161 全局辅助页族', () => {
  test('真实注册后走查 onboarding、命令面板、Analytics 与恢复页', async ({ page }, testInfo) => {
    const email = await register(page);
    const slug = await createWorkspace(page);

    const database = databaseEvidence(email, slug);
    expect(database).toEqual({
      user_count: 1,
      workspace_count: 1,
      active_membership_count: 1,
    });
    await testInfo.attach('mes161-postgresql-evidence', {
      body: Buffer.from(JSON.stringify(database, null, 2)),
      contentType: 'application/json',
    });

    await expect(page.getByTestId('onboarding-card')).toBeVisible();
    await expect(page.getByTestId('onboarding-progress')).toBeVisible();
    await evidence(page, 'onboarding-light');
    await page.getByTestId('onboarding-dismiss').click();
    await expect(page.getByTestId('onboarding-card')).toHaveCount(0);

    if (test.info().project.name === 'mobile') {
      await page.getByTestId('open-palette').click();
    } else {
      await page.keyboard.press('ControlOrMeta+K');
    }
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await expect(palette).toBeVisible();
    await expect(palette.getByRole('combobox')).toBeFocused();
    await expect(palette.getByRole('option', { name: 'Home', exact: true })).toBeVisible();
    await evidence(page, 'palette-light');
    await page.keyboard.press('Escape');
    await expect(palette.getByRole('combobox')).not.toBeFocused();
    await expect(palette).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);

    await page.goto(`/w/${slug}/insights`);
    await expect(page.getByTestId('insights-range')).toBeVisible();
    await expect(page.locator('.mesh-empty-state').first()).toBeVisible();
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
    await evidence(page, 'analytics-light');

    const forbiddenUrl = `/forbidden?workspace=${encodeURIComponent(`/w/${slug}`)}`;
    await page.goto(forbiddenUrl);
    await expect(page.getByTestId('forbidden-page')).toBeVisible();
    await expect(page.getByTestId('forbidden-contact')).toBeVisible();
    await expect(page.getByTestId('forbidden-home')).toBeVisible();
    await expect(page.getByTestId('forbidden-contact-action')).toHaveAttribute(
      'href',
      `/w/${slug}/members`,
    );
    await evidence(page, 'forbidden-light');
    await page.getByTestId('forbidden-contact-action').click();
    await page.waitForURL((url) => new URL(url).pathname === `/w/${slug}/members`);
    await expect(page.getByRole('heading', { level: 1, name: 'Members' })).toBeVisible();

    await page.goto(forbiddenUrl);
    await page.getByTestId('forbidden-home').click();
    await page.waitForURL((url) => new URL(url).pathname === '/');
    await expect(page.getByTestId(`home-workspace-${slug}`)).toBeVisible();

    await page.goto(`/w/${slug}/this-route-does-not-exist`);
    await expect(page.getByTestId('notfound-home')).toBeVisible();
    await expect(page.getByTestId('notfound-workspace')).toHaveAttribute('href', `/w/${slug}`);
    await evidence(page, 'not-found-light');

    await setTheme(page, 'dark');

    await page.goto(`/w/${slug}/insights`);
    await expect(page.getByTestId('insights-range')).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await evidence(page, 'analytics-dark');

    await page.goto(forbiddenUrl);
    await expect(page.getByTestId('forbidden-page')).toBeVisible();
    await expect(page.getByTestId('forbidden-contact-action')).toHaveAttribute(
      'href',
      `/w/${slug}/members`,
    );
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await evidence(page, 'forbidden-dark');

    await page.goto(`/w/${slug}/this-route-still-does-not-exist`);
    await expect(page.getByTestId('notfound-home')).toBeVisible();
    await expect(page.getByTestId('notfound-workspace')).toHaveAttribute('href', `/w/${slug}`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await evidence(page, 'not-found-dark');
  });
});
