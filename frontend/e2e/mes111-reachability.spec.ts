/**
 * MES-111 Phase 0 退出条件真实浏览器验证(design-quality §12 Phase 0):
 * 「所有主导航在 320px 可达;无死链、假搜索和页面级横向溢出」。
 *
 * 390×844(主流手机)真实走查 mock 契约栈:
 * - 底部主导航 + 「更多」抽屉承载全部主导航(隐藏侧栏有等价入口,A-03);
 * - /skills/marketplace 刷新直达、旧 /marketplace 兼容重定向(A-01);
 * - 顶栏搜索即统一搜索入口:键入/回车展开命令面板并携带查询(A-02);
 * - skip link 键盘首焦直达主内容(§10.2);
 * - 中文界面「自动值守 / 运行环境」两个不同导航条目(§4.1);
 * - 首页/看板/成员页无页面级横向溢出(A-04/A-05);
 * - 320×640 极窄视口底部导航仍可达。
 *
 * 走查截图存证于 e2e/evidence/mes111-shell/(验收 R1 目录名统一;md5 唯一性门禁)。
 */
import { expect, test } from '@playwright/test';
import { login } from './helpers';

const EVIDENCE_DIR = 'e2e/evidence/mes111-shell';

test.describe('手机可达性 @390×844', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('底部主导航四项可达,当前页 aria-current=page', async ({ page }) => {
    await login(page);
    await page.goto('/');
    const homeLink = page.getByTestId('mobile-nav-home');
    await expect(homeLink).toBeVisible();
    await expect(homeLink).toHaveAttribute('aria-current', 'page');

    await page.getByTestId('mobile-nav-issues').click();
    await page.waitForURL('**/issues');
    await page.getByTestId('mobile-nav-board').click();
    await page.waitForURL('**/board');
    await expect(page.getByTestId('mobile-nav-board')).toHaveAttribute('aria-current', 'page');
    await page.getByTestId('mobile-nav-chat').click();
    await page.waitForURL('**/chat');
  });

  test('「更多」抽屉承载全部次级导航,点选后关闭并跳转', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.getByTestId('mobile-nav-more').click();
    const drawer = page.getByRole('dialog', { name: 'All navigation' });
    await expect(drawer).toBeVisible();
    for (const key of [
      'inbox',
      'projects',
      'members',
      'skills',
      'squads',
      'cycles',
      'autopilots',
      // MES-115:含糊旧键 automation 清偿为明确的 runtimes(§4.1 运行环境独立入口)
      'runtimes',
      'insights',
      'integrations',
      'settings',
    ]) {
      await expect(page.getByTestId('mobile-drawer-nav-' + key)).toBeVisible();
    }
    await page.getByTestId('mobile-drawer-nav-members').click();
    await page.waitForURL('**/members');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.screenshot({ path: `${EVIDENCE_DIR}/phone-members-drawer-flow-light.png` });
  });

  test('/skills/marketplace 刷新直达(死链修复),旧 /marketplace 兼容重定向', async ({ page }) => {
    await login(page);
    await page.goto('/skills/marketplace');
    await expect(page.getByTestId('marketplace-title')).toBeVisible();
    await expect(page.getByText('Page not found')).toHaveCount(0);

    await page.goto('/marketplace');
    await expect(page).toHaveURL(/\/skills\/marketplace$/);
    await expect(page.getByTestId('marketplace-title')).toBeVisible();
  });

  test('顶栏搜索即统一入口:键入把查询交给完整命令面板;分层 Esc 后焦点回输入框', async ({
    page,
  }) => {
    await login(page);
    await page.goto('/');
    const topbarSearch = page.getByTestId('topbar-search');
    await topbarSearch.fill('theme');
    // §4.9:首字符即携查询把焦点交给完整命令面板,顶栏不保留第二份查询状态。
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    const paletteSearch = palette.getByRole('combobox');
    await expect(palette).toBeVisible();
    await expect(paletteSearch).toHaveValue('theme');
    await expect(topbarSearch).toHaveValue('');
    // 分层关闭:首个 Esc 只把焦点从查询框交给 dialog,第二个才关闭并恢复触发点。
    await page.keyboard.press('Escape');
    await expect(palette).toBeVisible();
    await expect(paletteSearch).not.toBeFocused();
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);
    await expect(topbarSearch).toBeFocused();
  });

  test('顶栏搜索回车同样展开命令面板', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.getByTestId('topbar-search').press('Enter');
    await expect(page.locator('.mesh-palette input')).toBeVisible();
  });

  test('skip link 键盘首焦可达,Enter 后焦点落主内容(§10.2)', async ({ page }) => {
    await login(page);
    await page.goto('/');
    // 新文档从顶部开始:首个 Tab 命中 skip link
    await page.keyboard.press('Tab');
    const focusedHref = await page.evaluate(() => document.activeElement?.getAttribute('href'));
    expect(focusedHref).toBe('#mesh-main-content');
    await page.keyboard.press('Enter');
    const focusedId = await page.evaluate(() => document.activeElement?.id);
    expect(focusedId).toBe('mesh-main-content');
  });

  test('中文界面:「自动值守」与「运行环境」为两个不同导航条目(§4.1 去重名)', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
    await page.goto('/');
    await page.getByTestId('mobile-nav-more').click();
    await expect(page.getByTestId('mobile-drawer-nav-autopilots')).toHaveText('自动值守');
    await expect(page.getByTestId('mobile-drawer-nav-runtimes')).toContainText('运行环境');
    await page.screenshot({ path: `${EVIDENCE_DIR}/phone-drawer-zh-nav-light.png` });
  });

  test('首页/看板/成员页无页面级横向溢出(A-04/A-05)', async ({ page }) => {
    await login(page);
    for (const path of ['/', '/board', '/members']) {
      await page.goto(path);
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(
        overflow.scrollWidth,
        `${path} 页面级横向溢出:scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.clientWidth}`,
      ).toBeLessThanOrEqual(overflow.clientWidth);
    }
  });

  test('看板手机形态:整体不超视口、列容器在视口内(存证)', async ({ page }) => {
    await login(page);
    await page.goto('/board');
    await page.getByTestId('board-page').waitFor({ state: 'visible' });
    const pageBox = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(pageBox.scrollWidth).toBeLessThanOrEqual(pageBox.clientWidth);
    const boardBox = await page.locator('.mesh-board').boundingBox();
    expect(boardBox).not.toBeNull();
    if (boardBox !== null) {
      expect(boardBox.x).toBeGreaterThanOrEqual(0);
      expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(pageBox.clientWidth + 1);
    }
    await page.screenshot({ path: `${EVIDENCE_DIR}/phone-board-light.png`, fullPage: true });
  });

  test('暗色主题下底部导航与抽屉仍完整可用(双主题走查)', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.goto('/');
    await expect(page.getByTestId('mobile-nav-home')).toBeVisible();
    // phone-home-dark 由 FOUNDATION_EVIDENCE_DIR 单一存证(见下方块);此处不再重复写
    // mes111-shell,否则 evidence-unique 门禁报跨目录 md5 重复。
    await page.getByTestId('mobile-nav-more').click();
    await expect(page.getByRole('dialog', { name: 'All navigation' })).toBeVisible();
    await page.screenshot({ path: `${EVIDENCE_DIR}/phone-drawer-dark.png` });
  });
});

test.describe('桌面端回归 @1280×720(双主题走查存证)', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test('桌面侧栏导航完整,skip link/搜索入口在场;亮暗各一张存证', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await expect(page.getByTestId('nav-home')).toBeVisible();
    await expect(page.getByTestId('nav-skills')).toBeVisible();
    await expect(page.getByTestId('topbar-search')).toBeVisible();
    await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-home-light.png` });

    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.goto('/');
    await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-home-dark.png` });
  });
});

test.describe('手机可达性 @320×640(极窄视口)', () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test('320px 下底部导航五入口可达且无页面级横向溢出', async ({ page }) => {
    await login(page);
    await page.goto('/');
    for (const key of ['home', 'issues', 'board', 'chat', 'more']) {
      await expect(page.getByTestId('mobile-nav-' + key)).toBeVisible();
    }
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
    await page.screenshot({ path: `${EVIDENCE_DIR}/phone-home-320-light.png` });
  });
});

const FOUNDATION_EVIDENCE_DIR = 'e2e/evidence/mes111-foundation';

test.describe('Phase 1 设计系统底座:双端双主题走查存证', () => {
  test('桌面 1440×900 首页亮/暗:新令牌体系(表面分层/强调色/排版节奏)真实渲染', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await login(page);
    await page.goto('/');
    await expect(page.getByTestId('nav-home')).toBeVisible();
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/desktop-home-light.png` });

    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.goto('/');
    await expect(page.getByTestId('nav-home')).toBeVisible();
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/desktop-home-dark.png` });
  });

  test('桌面登录页亮/暗:PublicFlow 框架随底座令牌升级(暗色经持久化偏好预置)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/login');
    await expect(page.locator('input[type="email"], input[name="email"]').first()).toBeVisible();
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/desktop-login-light.png` });

    // 暗色:经 mesh.settings.v1 持久化偏好预置(theme.md 协商链,防闪烁分区承载)
    await page.addInitScript(() => {
      localStorage.setItem(
        'mesh.settings.v1',
        JSON.stringify({ state: { preferences: { theme: 'dark' } }, version: 2 }),
      );
    });
    await page.goto('/login');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/desktop-login-dark.png` });
  });

  test('手机 390×844 首页/看板亮/暗:令牌体系在紧凑视口一致呈现且无横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await page.goto('/');
    await expect(page.getByTestId('mobile-nav-home')).toBeVisible();
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/phone-home-light.png` });

    await page.goto('/board');
    await page.getByTestId('board-page').waitFor({ state: 'visible' });
    const lightOverflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(lightOverflow.scrollWidth).toBeLessThanOrEqual(lightOverflow.clientWidth);
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/phone-board-light.png` });

    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.goto('/');
    await expect(page.getByTestId('mobile-nav-home')).toBeVisible();
    await page.screenshot({ path: `${FOUNDATION_EVIDENCE_DIR}/phone-home-dark.png` });
  });
});
