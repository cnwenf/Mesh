/**
 * MES-108 shared visual-foundation smoke test against the real API/database.
 *
 * This is deliberately separate from the model-card screenshot matrix: it proves
 * that the accepted shell/tokens are applied by the running React application,
 * but it does not manufacture or approve any visual-baseline evidence.
 */
import { expect, test } from '@playwright/test';
import type { Page, TestInfo } from '@playwright/test';

const PASSWORD = 'Mesh-Demo#2026x';

function uniqueEmail(testInfo: TestInfo): string {
  return `mes108-foundation-${testInfo.project.name}-${Date.now()}-${process.pid}@example.com`;
}

async function rootTokens(page: Page, theme: 'light' | 'dark'): Promise<Record<string, string>> {
  return page.evaluate((nextTheme) => {
    document.documentElement.dataset.theme = nextTheme;
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(
      [
        '--color-bg',
        '--color-canvas',
        '--color-surface',
        '--color-primary',
        '--color-primary-contrast',
        '--color-input-border-base',
        '--color-input-border',
        '--color-input-border-hover',
        '--color-text-faint-base',
        '--color-placeholder',
        '--shell-sidebar-expanded',
        '--radius-lg',
        '--radius-xl',
      ].map((name) => [name, style.getPropertyValue(name).trim()]),
    );
  }, theme);
}

test('real React shell applies the accepted MES-108 token foundation', async ({
  page,
}, testInfo) => {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();

  await expect
    .poll(() => rootTokens(page, 'light'))
    .toEqual({
      '--color-bg': '#f3f3f4',
      '--color-canvas': '#fbfbfb',
      '--color-surface': '#ffffff',
      '--color-primary': '#18181b',
      '--color-primary-contrast': '#fafafa',
      '--color-input-border-base': '#e4e4e7',
      '--color-input-border': '#8b8b94',
      '--color-input-border-hover': '#71717a',
      '--color-text-faint-base': '#81818b',
      '--color-placeholder': '#64636e',
      '--shell-sidebar-expanded': '256px',
      '--radius-lg': '10px',
      '--radius-xl': '14px',
    });
  await expect(page.getByTestId('login-email')).toHaveCSS('border-color', 'rgb(139, 139, 148)');
  if (testInfo.project.name === 'wide') {
    await page.getByTestId('login-email').hover();
    await expect(page.getByTestId('login-email')).toHaveCSS('border-color', 'rgb(113, 113, 122)');
    await page.mouse.move(0, 0);
  }

  await expect
    .poll(() => rootTokens(page, 'dark'))
    .toEqual({
      '--color-bg': '#0c0c0e',
      '--color-canvas': '#111114',
      '--color-surface': '#18181b',
      '--color-primary': '#e4e4e7',
      '--color-primary-contrast': '#18181b',
      '--color-input-border-base': 'rgb(255 255 255 / 15%)',
      '--color-input-border': '#71717a',
      '--color-input-border-hover': '#8b8b94',
      '--color-text-faint-base': '#7f7f89',
      '--color-placeholder': '#9f9fa9',
      '--shell-sidebar-expanded': '256px',
      '--radius-lg': '10px',
      '--radius-xl': '14px',
    });
  await expect(page.getByTestId('login-email')).toHaveCSS('border-color', 'rgb(113, 113, 122)');

  await rootTokens(page, 'light');
  const email = uniqueEmail(testInfo);
  await page.getByTestId('login-display-name').fill('MES-108 E2E');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  const [registerResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes('/api/v1/auth/register') && response.request().method() === 'POST',
    ),
    page.getByTestId('login-account-submit').click(),
  ]);
  expect(registerResponse.status()).toBe(201);
  await expect(page.getByTestId('register-verify-sent')).toContainText(email);
  await page.getByTestId('register-continue').click();

  await page.waitForURL((url) => new URL(url).pathname === '/');
  await expect(page.locator('.mesh-shell')).toBeVisible();
  await expect(page.getByTestId('home-greeting')).toContainText('MES-108 E2E');
  await expect(page.getByTestId('home-no-workspaces')).toBeVisible();
  await expect
    .poll(() =>
      page
        .getByTestId('topbar-search')
        .evaluate((element) => getComputedStyle(element, '::placeholder').color),
    )
    .toBe('rgb(100, 99, 110)');

  const createWorkspace = page.getByTestId('home-create-workspace');
  await expect(createWorkspace).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(createWorkspace).toHaveCSS('color', 'rgb(250, 250, 250)');
  if (testInfo.project.name === 'wide') {
    await createWorkspace.hover();
    await page.mouse.down();
    await expect(createWorkspace).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 1)');
    await page.mouse.up();
  }

  if (testInfo.project.name === 'phone') {
    await expect(page.locator('.mesh-sidebar')).not.toBeVisible();
    await expect(page.locator('.mesh-mobile-nav')).toBeVisible();
  } else {
    await expect(page.locator('.mesh-sidebar')).toBeVisible();
    await expect(page.locator('.mesh-sidebar')).toHaveCSS('width', '256px');
    await expect(page.locator('.mesh-mobile-nav')).not.toBeVisible();
  }

  await rootTokens(page, 'dark');
  await expect
    .poll(() =>
      page
        .getByTestId('topbar-search')
        .evaluate((element) => getComputedStyle(element, '::placeholder').color),
    )
    .toBe('rgb(159, 159, 169)');
  await expect(createWorkspace).toHaveCSS('background-color', 'rgb(228, 228, 231)');
  await expect(createWorkspace).toHaveCSS('color', 'rgb(24, 24, 27)');
});
