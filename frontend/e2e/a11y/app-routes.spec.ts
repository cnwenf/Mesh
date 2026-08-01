import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page, Response } from '@playwright/test';
import { APP_ROUTE_MANIFEST } from '../../src/shell/appRouteManifest';
import type { AppRouteManifestEntry } from '../../src/shell/appRouteManifest';
import { PAGES, VISUAL_TOKEN } from '../visual/visual-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const EXTENDED_ROUTES = APP_ROUTE_MANIFEST.filter((route) => route.browser.level === 'extended');
const PROTECTED_ROUTES = APP_ROUTE_MANIFEST.filter((route) => route.access === 'protected');
const PUBLIC_ROUTES = APP_ROUTE_MANIFEST.filter((route) => route.access === 'public');
const PERMISSION_ROUTES = APP_ROUTE_MANIFEST.filter((route) =>
  ['human', 'workspace_admin', 'workspace_owner'].includes(route.permission),
);
const REDIRECT_ROUTES = APP_ROUTE_MANIFEST.filter((route) => route.browser.level === 'redirect');

function pathname(path: string): string {
  return new URL(path, 'http://route-manifest.local').pathname;
}

function pathAndSearch(path: string): string {
  const url = new URL(path, 'http://route-manifest.local');
  return url.pathname + url.search;
}

async function injectSession(page: Page, authenticated: boolean): Promise<void> {
  await page.addInitScript(
    ({ token, signedIn }) => {
      if (signedIn) {
        window.localStorage.setItem(
          'mesh.auth.v1',
          JSON.stringify({ state: { token, refreshToken: null }, version: 0 }),
        );
      } else {
        window.localStorage.removeItem('mesh.auth.v1');
      }
      window.localStorage.setItem(
        'mesh.settings.v1',
        JSON.stringify({
          state: { preferences: { theme: 'light', locale: 'zh-CN', timezone: 'UTC' } },
          version: 2,
        }),
      );
    },
    { token: VISUAL_TOKEN, signedIn: authenticated },
  );
}

function apiFailureCollector(page: Page): { failures: string[]; stop: () => void } {
  const failures: string[] = [];
  const listener = (response: Response): void => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      failures.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  };
  page.on('response', listener);
  return { failures, stop: () => page.off('response', listener) };
}

async function waitForNormalState(page: Page, route: AppRouteManifestEntry): Promise<void> {
  if (route.browser.level !== 'extended') throw new Error(`${route.id} is not an extended route`);
  const api = apiFailureCollector(page);
  try {
    await page.goto(route.samplePath);
    await page.locator(route.browser.readySelector).waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    expect(api.failures, `${route.id} reached an API failure instead of a normal state`).toEqual(
      [],
    );
    // /styleguide intentionally demonstrates the ErrorState primitive; no other normal route may
    // satisfy readiness by rendering an API-error page.
    await expect(page.locator('.mesh-error-state')).toHaveCount(route.id === 'styleguide' ? 1 : 0);
  } finally {
    api.stop();
  }
}

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

test('the 13 core-page registry exactly matches manifest core coverage', async ({
  page: _page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one project owns the registry contract');
  expect(
    APP_ROUTE_MANIFEST.filter((route) => route.browser.level === 'core')
      .map((route) => pathAndSearch(route.samplePath))
      .sort(),
  ).toEqual(
    Object.values(PAGES)
      .map((route) => route.path)
      .sort(),
  );
});

test('all 63 manifest routes enforce their declared public/protected access', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one project owns the exhaustive access crawl');
  test.setTimeout(180_000);
  await injectSession(page, false);

  for (const route of PROTECTED_ROUTES) {
    await test.step(`${route.id} is protected`, async () => {
      await page.goto(route.samplePath);
      await expect(page).toHaveURL((url) => {
        const next = url.searchParams.get('next');
        return url.pathname === '/login' && next === pathAndSearch(route.samplePath);
      });
    });
  }

  for (const route of PUBLIC_ROUTES) {
    await test.step(`${route.id} is public`, async () => {
      // OAuth callback writes a token before redirecting home. Reset anonymous state before each
      // route so one public-route assertion cannot affect the next one in the same crawl.
      await injectSession(page, false);
      await page.goto(route.samplePath);
      const expectedPath =
        route.browser.level === 'extended' && route.browser.expectedPath !== undefined
          ? route.browser.expectedPath
          : pathname(route.samplePath);
      await expect(page).toHaveURL((url) => url.pathname === expectedPath);
    });
  }
});

test('workspace and human-only routes enforce their declared permission state', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one project owns the permission crawl');
  test.setTimeout(120_000);
  await injectSession(page, true);

  const memberWorkspace = {
    id: 'ws-1',
    name: 'Acme',
    slug: 'acme',
    logo_url: null,
    timezone: 'UTC',
    settings: { default_locale: 'zh-CN', default_theme: 'light' },
    my_role: 'member',
    created_at: '2026-07-25T08:00:00.000Z',
    updated_at: '2026-07-25T08:00:00.000Z',
  };
  await page.route('**/api/v1/workspaces/by-slug/acme', async (requestRoute) => {
    await requestRoute.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({ data: memberWorkspace }),
    });
  });
  for (const route of PERMISSION_ROUTES.filter((entry) => entry.permission === 'workspace_admin')) {
    await test.step(`${route.id} denies a workspace member`, async () => {
      await page.goto(route.samplePath);
      await expect(page.getByTestId('ws-settings-denied')).toBeVisible();
    });
  }

  // An admin clears the parent gate but still cannot enter the owner-only danger page.
  memberWorkspace.my_role = 'admin';
  const danger = PERMISSION_ROUTES.find((route) => route.permission === 'workspace_owner');
  if (danger === undefined) throw new Error('owner-only route is missing');
  await page.goto(danger.samplePath);
  await expect(page.getByTestId('ws-danger-denied')).toBeVisible();

  await page.unroute('**/api/v1/workspaces/by-slug/acme');
  await page.route('**/api/v1/me', async (requestRoute) => {
    await requestRoute.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify({
        data: {
          kind: 'agent',
          id: 'member-agent-1',
          member_type: 'agent',
          workspace_id: 'ws-1',
          role: 'member',
          name: 'Mesh Agent',
          scopes: [],
        },
      }),
    });
  });
  for (const route of PERMISSION_ROUTES.filter((entry) => entry.permission === 'human')) {
    await test.step(`${route.id} denies an agent principal`, async () => {
      await page.goto(route.samplePath);
      await expect(page.getByTestId('approvals-agent-gated')).toBeVisible();
    });
  }
});

test('all static redirects reach their declared canonical paths', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one project owns redirect semantics');
  await injectSession(page, true);
  for (const route of REDIRECT_ROUTES) {
    if (route.browser.level !== 'redirect') continue;
    const expectedPath = route.browser.expectedPath;
    await test.step(route.id, async () => {
      await page.goto(route.samplePath);
      await expect(page).toHaveURL((url) => url.pathname === expectedPath);
    });
  }
});

for (const route of EXTENDED_ROUTES) {
  test(`${route.id} reaches a normal state with no detectable WCAG A/AA violations`, async ({
    page,
  }) => {
    const authenticated =
      route.access === 'protected' ||
      (route.browser.level === 'extended' && route.browser.authenticated === true);
    await injectSession(page, authenticated);
    await waitForNormalState(page, route);
    const results = await new AxeBuilder({ page }).withTags([...WCAG_TAGS]).analyze();
    expect(results.violations, violationSummary(results.violations)).toEqual([]);
  });
}

test('all extended non-core routes reflow at every §13 viewport', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'phone-touch', 'phone-touch project owns the reflow crawl');
  test.setTimeout(420_000);
  // Every extended public route remains public while signed in; one stable session avoids stacking
  // conflicting init scripts as the crawl switches between public and protected URLs.
  await injectSession(page, true);
  for (const width of [320, 390, 640, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? (width === 320 ? 640 : 844) : 900 });
    for (const route of EXTENDED_ROUTES) {
      await waitForNormalState(page, route);
      const geometry = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(
        geometry.scrollWidth,
        `${route.id}@${width}px overflows (${geometry.scrollWidth} > ${geometry.clientWidth})`,
      ).toBeLessThanOrEqual(geometry.clientWidth);
    }
  }
});
