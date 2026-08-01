/**
 * UI/UX 基线真实浏览器验证(MES-16 §真实操作验证):
 * 主题切换、i18n 切换、快捷键框架与命令面板、404、登录守卫、时区化展示、异常态组件。
 *
 * MES-106 起受保护页位于登录守卫之后:基线用例经 helpers.login 真实邮箱/密码登录
 * 后再操作;「未登录访问受保护页」的基线断言改为守卫跳转 /login?next=(auth.md §4.1)。
 */
import { expect, test } from '@playwright/test';
import { login } from './helpers';

test.describe('主题切换(README §6.12:即时生效、无刷新、暗色整组 token 替换)', () => {
  test('设置页选择暗色 → html[data-theme=dark],切回亮色恢复', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByTestId('theme-select').selectOption('light');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('暗色主题在刷新后保持(持久化)', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    const persisted = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/v1/users/me') &&
        response.request().method() === 'PATCH' &&
        response.ok(),
    );
    await page.getByTestId('theme-select').selectOption('dark');
    await persisted;
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });
});

test.describe('i18n 切换(README §6.18:就地更新、无刷新、ICU 复数)', () => {
  test('切换到 zh-CN → 界面文案就地变更', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '语言' })).toBeVisible();
  });

  test('恢复跟随默认(null locale)', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
    await page.getByTestId('locale-select').selectOption('');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  });

  test('时区化展示:同一 UTC 值按用户时区渲染(§6.18 存储不变)', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByTestId('timezone-select').selectOption('Asia/Shanghai');
    const sample = page.getByTestId('tz-sample');
    await expect(sample).toContainText('2026-07-26 02:00');
    await expect(sample).toContainText('GMT+8');
    await page.getByTestId('timezone-select').selectOption('America/New_York');
    await expect(sample).toContainText('2026-07-25 14:00');
    await expect(sample).toContainText('GMT-4');
  });
});

test.describe('快捷键体系与命令面板(README §6.12)', () => {
  test('? 打开快捷键帮助层,Esc 关闭', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.keyboard.press('Shift+/');
    const help = page.getByRole('dialog', { name: 'Keyboard shortcuts' });
    await expect(help).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(help).toBeHidden();
  });

  test('Ctrl+K 打开命令面板,搜索并执行导航命令', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await expect(palette).toBeVisible();
    await palette.getByRole('combobox').fill('Settings');
    await page.keyboard.press('Enter');
    await page.waitForURL('**/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  });

  test('Ctrl+K 搜索任意关键词均出结果且 Issues 导航命令可命中跳转(MES-45 回归)', async ({
    page,
  }) => {
    await login(page);
    await page.goto('/');
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    const input = palette.getByRole('combobox');
    // 上一轮回归:任一导航命令 label 缺失 → 输入即抛错,结果塌成 0 条
    await input.fill('home');
    await expect(palette.getByRole('option').first()).toBeVisible();
    await input.fill('Issues');
    await expect(palette.getByRole('option', { name: 'Issues' })).toBeVisible();
    await page.keyboard.press('Enter');
    await page.waitForURL('**/issues');
  });

  test('G 然后 I 序列键跳转收件箱;输入框聚焦时不触发裸键', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.keyboard.press('g');
    await page.keyboard.press('i');
    await page.waitForURL('**/inbox');
    // 输入框聚焦时裸键豁免:在搜索框内敲 g i 不应再次导航
    await page.goto('/');
    await page.getByTestId('topbar-search').focus();
    await page.keyboard.type('gi');
    await expect(page).toHaveURL(/\/$/);
  });

  test('顶栏按钮提供等价鼠标路径(快捷键是加速,不是唯一入口)', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.getByTestId('open-palette').click();
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
    // §4.5 Esc 分层关闭栈:输入框获焦时首个 Esc 仅失焦,第二个 Esc 才关面板。
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Command palette' })).not.toBeVisible();
    await page.getByTestId('open-help').click();
    await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
  });
});

test.describe('路由与登录守卫', () => {
  test('未登录访问受保护首页 → 守卫跳 /login?next=(MES-106);真实登录后回跳真实首页(MES-107)', async ({
    page,
  }) => {
    await page.goto('/');
    // 路由守卫:受保护页未登录不再渲染,统一跳登录页并携带原目标(§4.1)
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).searchParams.get('next')).toBe('/');
    await expect(page.getByRole('heading', { name: 'Sign in to Mesh' })).toBeVisible();
    // 登录页无 dev 令牌块 / 过时阶段文案(脚手架已清理)
    await expect(page.locator('.mesh-login__dev')).toHaveCount(0);
    await expect(page.getByText(/Phase 2/)).toHaveCount(0);
    // 真实邮箱/密码登录 → 回跳原目标 → 真实首页(工作区列表)
    await page.getByTestId('login-email').fill('jane@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    await page.getByTestId('login-account-submit').click();
    await page.waitForURL('**/');
    await expect(page.getByTestId('home-workspace-list')).toBeVisible();
    await expect(page.getByTestId('home-dashboard')).toBeVisible();
  });

  test('404 页与返回首页入口', async ({ page }) => {
    await login(page);
    await page.goto('/definitely-not-a-page');
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await page.getByTestId('notfound-home').click();
    await page.waitForURL('**/');
  });

  test('侧栏导航基线:看板为数据页(MES-43)呈 §6.12 错误态,成员页呈标题', async ({ page }) => {
    await login(page);
    await page.goto('/');
    const main = page.locator('main');
    // 看板为视图定义层数据页:mock 契约提供 /users/me 但不提供视图端点,
    // 页面按 README §6.12 呈现错误态基线(错误标题 + 重试入口)
    await page.getByTestId('nav-board').click();
    await page.waitForURL('**/board');
    await expect(page.getByTestId('board-page')).toBeVisible();
    await expect(main.getByText('Something went wrong')).toBeVisible();
    await expect(main.getByRole('button', { name: /retry/i })).toBeVisible();
    await page.getByTestId('nav-members').click();
    await page.waitForURL('**/members');
    // mock 提供 /users/me:成员页解析工作区后呈标题(mock 无名册端点 → 空/错误态)
    await expect(main.getByRole('heading', { name: 'Members' })).toBeVisible();
  });
});
