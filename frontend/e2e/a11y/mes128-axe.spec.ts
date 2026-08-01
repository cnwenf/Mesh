import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { PAGES, VISUAL_TOKEN } from '../visual/visual-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const EVIDENCE_DIR = 'e2e/evidence/mes111-b5';

function violationSummary(
  violations: Awaited<ReturnType<AxeBuilder['analyze']>>['violations'],
): string {
  return violations
    .map(
      (violation) =>
        `${violation.id} (${violation.impact ?? 'unknown'}): ` +
        violation.nodes.map((node) => node.target.join(' ')).join(', '),
    )
    .join('\n');
}

async function injectAuthenticatedTheme(page: import('@playwright/test').Page): Promise<void> {
  await page.addInitScript((token: string) => {
    if (window.location.pathname === '/login') {
      window.localStorage.removeItem('mesh.auth.v1');
    } else {
      window.localStorage.setItem(
        'mesh.auth.v1',
        JSON.stringify({ state: { token, refreshToken: null }, version: 0 }),
      );
    }
    if (window.localStorage.getItem('mesh.settings.v1') === null) {
      window.localStorage.setItem(
        'mesh.settings.v1',
        JSON.stringify({
          state: { preferences: { theme: 'light', locale: 'zh-CN', timezone: 'UTC' } },
          version: 2,
        }),
      );
    }
  }, VISUAL_TOKEN);
}

async function readyForCorePage(
  page: import('@playwright/test').Page,
  name: string,
  pageSpec: (typeof PAGES)[string],
  compact: boolean,
): Promise<void> {
  if (compact && name === '看板') {
    await page.getByTestId('board-compact').waitFor({ state: 'visible' });
  } else if (compact && name === '成员') {
    await page.getByTestId('member-card-member-human-1').waitFor({ state: 'visible' });
  } else {
    await pageSpec.ready(page);
  }
}

for (const [name, pageSpec] of Object.entries(PAGES)) {
  test(`${name} has no detectable WCAG A/AA violations`, async ({ page }, testInfo) => {
    await injectAuthenticatedTheme(page);
    await page.goto(pageSpec.path);
    await readyForCorePage(page, name, pageSpec, testInfo.project.name === 'phone-touch');
    if (pageSpec.interact !== undefined) await pageSpec.interact(page);
    const results = await new AxeBuilder({ page }).withTags([...WCAG_TAGS]).analyze();
    expect(results.violations, violationSummary(results.violations)).toEqual([]);
  });
}

test('core pages reflow at 320/390 and the 200% desktop equivalent without page overflow', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'phone-touch', 'phone-touch project owns the reflow crawl');
  await injectAuthenticatedTheme(page);
  for (const width of [320, 390, 640]) {
    await page.setViewportSize({ width, height: width === 320 ? 640 : 844 });
    for (const [name, pageSpec] of Object.entries(PAGES)) {
      await page.goto(pageSpec.path);
      await readyForCorePage(page, name, pageSpec, width < 600);
      const geometry = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(
        geometry.scrollWidth,
        `${name}@${width}px has page overflow (${geometry.scrollWidth} > ${geometry.clientWidth})`,
      ).toBeLessThanOrEqual(geometry.clientWidth);
    }
  }
});

test('coarse-pointer controls meet the 44px target floor on core pages', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'phone-touch', 'phone-touch project owns target-size checks');
  await injectAuthenticatedTheme(page);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, pageSpec] of Object.entries(PAGES)) {
    await page.goto(pageSpec.path);
    await readyForCorePage(page, name, pageSpec, true);
    const undersized = await page
      .locator('button, input, select, textarea, [role="tab"], [role="menuitem"]')
      .evaluateAll((elements) =>
        elements.flatMap((element) => {
          const node = element as HTMLElement;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          if (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            rect.width === 0 ||
            rect.height === 0 ||
            (element instanceof HTMLInputElement &&
              ['hidden', 'checkbox', 'radio'].includes(element.type))
          ) {
            return [];
          }
          return rect.width + 0.5 < 44 || rect.height + 0.5 < 44
            ? [
                `${node.tagName.toLowerCase()}${node.dataset.testid ? `[data-testid=${node.dataset.testid}]` : ''}=${Math.round(rect.width)}x${Math.round(rect.height)}`,
              ]
            : [];
        }),
      );
    expect(undersized, `${name} has undersized coarse-pointer controls`).toEqual([]);
  }
});

test('desktop/mobile × light/dark core-page evidence matrix', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'phone-touch', 'one project writes each evidence file once');
  await injectAuthenticatedTheme(page);
  for (const [mode, viewport] of [
    ['mobile', { width: 390, height: 844 }],
    ['desktop', { width: 1440, height: 900 }],
  ] as const) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark'] as const) {
      // 走真实设置控件更新 Zustand + 持久层，避免只改 localStorage 后被当前页面
      // 的内存态回写覆盖。随后每张图都断言主题已落定，杜绝亮暗伪双份证据。
      await page.goto('/settings/appearance');
      await PAGES['设置'].ready(page);
      await page.getByTestId('theme-select').selectOption(theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      for (const [name, pageSpec] of Object.entries(PAGES)) {
        await page.goto(pageSpec.path);
        await readyForCorePage(page, name, pageSpec, mode === 'mobile');
        if (pageSpec.interact !== undefined) await pageSpec.interact(page);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        await page.screenshot({
          path: `${EVIDENCE_DIR}/${mode}-${pageSpec.snapshotKey}-${theme}.png`,
          fullPage: true,
        });
      }
    }
  }
});
