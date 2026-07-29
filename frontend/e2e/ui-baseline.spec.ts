/**
 * UI/UX 基线真实浏览器验证(MES-16 §真实操作验证):
 * 主题切换、i18n 切换、快捷键框架与命令面板、404、登录占位页、时区化展示、异常态组件。
 */
import { expect, test } from '@playwright/test';

test.describe('主题切换(README §6.12:即时生效、无刷新、暗色整组 token 替换)', () => {
  test('设置页选择暗色 → html[data-theme=dark],切回亮色恢复', async ({ page }) => {
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByTestId('theme-select').selectOption('light');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('首页演示区按钮切换 system 模式', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('demo-theme-dark').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByTestId('demo-theme-system').click();
    // Playwright 默认浅色系统偏好 → system 解析为 light
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('暗色主题在刷新后保持(持久化)', async ({ page }) => {
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });
});

test.describe('i18n 切换(README §6.18:就地更新、无刷新、ICU 复数)', () => {
  test('切换到 zh-CN → 界面文案就地变更', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '语言' })).toBeVisible();
  });

  test('恢复跟随默认(null locale)', async ({ page }) => {
    await page.goto('/settings');
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
    await page.getByTestId('locale-select').selectOption('');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  });

  test('ICU 复数:en 1 comment / 3 comments / No comments;zh 条评论', async ({ page }) => {
    await page.goto('/');
    const icu = page.getByTestId('demo-icu');
    await page.getByTestId('demo-count').fill('1');
    await expect(icu).toHaveText('1 comment');
    await page.getByTestId('demo-count').fill('3');
    await expect(icu).toHaveText('3 comments');
    await page.getByTestId('demo-count').fill('0');
    await expect(icu).toHaveText('No comments');
    await page.getByTestId('demo-locale-zh').click();
    await page.getByTestId('demo-count').fill('5');
    await expect(icu).toHaveText('5 条评论');
  });

  test('时区化展示:同一 UTC 值按用户时区渲染(§6.18 存储不变)', async ({ page }) => {
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
    await page.goto('/');
    await page.keyboard.press('Shift+/');
    const help = page.getByRole('dialog', { name: 'Keyboard shortcuts' });
    await expect(help).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(help).toBeHidden();
  });

  test('Ctrl+K 打开命令面板,搜索并执行导航命令', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await expect(palette).toBeVisible();
    await palette.getByRole('combobox').fill('Settings');
    await page.keyboard.press('Enter');
    await page.waitForURL('**/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  });

  test('Ctrl+K 搜索任意关键词均出结果且 Issues 导航命令可命中跳转(MES-45 回归)', async ({ page }) => {
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

test.describe('路由与占位页', () => {
  test('未登录访问首页可见(骨架不强制登录),登录占位页写 token 后进入', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('demo-theme')).toBeVisible();
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: 'Sign in to Mesh' })).toBeVisible();
    // dev-token 直填入口在 <details> 内(MES-26 起默认折叠),展开后填写
    await page.locator('.mesh-login__dev').evaluate((el) => {
      (el as HTMLDetailsElement).open = true;
    });
    await page.getByTestId('login-token').fill('e2e-token');
    await page.getByTestId('login-submit').click();
    await page.waitForURL('**/');
  });

  test('404 页与返回首页入口', async ({ page }) => {
    await page.goto('/definitely-not-a-page');
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await page.getByTestId('notfound-home').click();
    await page.waitForURL('**/');
  });

  test('侧栏导航基线:旧扁平路由经工作区解析,无上下文时呈 not-found(§3.4 迁移契约)', async ({ page }) => {
    await page.goto('/');
    const main = page.locator('main');
    // §3.4:扁平路由经前端路由器 replace navigation 迁移至规范路由,active
    // workspace 解析序 URL > 最近活跃 > 服务端 > 单一成员 > 选择页。mock 契约
    // 不提供 /users/me 且无本地记忆 → 解析失败 → not-found 基线(未登录态)。
    await page.getByTestId('nav-board').click();
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await page.getByTestId('nav-members').click();
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await expect(main.getByTestId('notfound-home')).toBeVisible();
  });
});

test.describe('异常态组件基线(README §6.12 异常态矩阵)', () => {
  test('首页演示区呈现 loading/empty/error 三态', async ({ page }) => {
    await page.goto('/');
    const states = page.getByTestId('demo-states');
    await expect(states).toBeVisible();
    // error 态提供重试入口
    await expect(states.getByRole('button', { name: /retry/i })).toBeVisible();
  });
});
