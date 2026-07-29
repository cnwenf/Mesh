/**
 * MES-79 命令面板实体搜索 e2e(mock 契约套件,search-command-palette.md §5.1):
 * - Ctrl+K 打开面板;空态呈现收藏区(§4.2.1 唯一数据流);
 * - 输入查询 → 分组结果(组头 + 选项)流式呈现;
 * - Enter 直达规范深链(§3.4,URL 断言);
 * - 顶栏搜索框输入即展开同一面板(§4.9 键鼠一致)。
 */
import { expect, test } from '@playwright/test';
import { gotoHomeReady, login, resetMockServer } from './helpers';

test.beforeEach(async () => {
  await resetMockServer();
});

test('Ctrl+K 打开面板:空态收藏区 + 分组搜索 + Enter 规范深链', async ({ page }) => {
  await login(page);
  await gotoHomeReady(page);

  // Ctrl+K 打开,搜索框即聚焦
  await page.keyboard.press('Control+K');
  const combobox = page.getByRole('combobox');
  await expect(combobox).toBeFocused();

  // 空态:收藏区(mock favorites 1 条)+ 命令区
  await expect(page.getByText('Favorites')).toBeVisible();
  await expect(page.getByText('Commands')).toBeVisible();

  // 输入查询 → 分组结果(mock fixture 两条 issue 标题含 Login)
  await combobox.fill('Login');
  await expect(page.getByText('Issues')).toBeVisible();
  const firstOption = page.getByRole('option', { name: /Login page crashes/ });
  await expect(firstOption).toBeVisible();
  // 命中高亮片段(字重 + 下划线叠加,非颜色唯一信号)
  await expect(page.locator('.mesh-palette__hit').first()).toHaveText('Login');

  // Enter 直达规范深链(§3.4:issue 按编号)
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/w\/acme\/issues\/by-identifier\/WEB-124/);
});

test('顶栏搜索输入即展开同一面板(§4.9)', async ({ page }) => {
  await login(page);
  await gotoHomeReady(page);

  const topbarSearch = page.getByTestId('topbar-search');
  await topbarSearch.pressSequentially('Login', { delay: 30 });

  // 首个字符即展开面板,后续输入落在面板搜索框
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('combobox')).toHaveValue('Login');
  await expect(page.getByRole('option', { name: /Login page crashes/ })).toBeVisible();
});

test('无匹配时呈现 no-results(文案 + 建议)', async ({ page }) => {
  await login(page);
  await gotoHomeReady(page);

  await page.keyboard.press('Control+K');
  const combobox = page.getByRole('combobox');
  await combobox.fill('zzz-nomatch');
  await expect(page.getByText(/No results for/)).toBeVisible();
  await expect(page.getByRole('option')).toHaveCount(0);
});
