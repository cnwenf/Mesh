import { expect, test as base } from '@playwright/test';
import { createHash } from 'node:crypto';
import { relative, resolve, sep } from 'node:path';

export function repositoryRelativePath(path, cwd = process.cwd()) {
  const normalized = relative(cwd, resolve(cwd, path)).split(sep).join('/');
  if (normalized.length === 0 || normalized === '..' || normalized.startsWith('../')) {
    throw new Error('MES-108 screenshot output must stay inside the frontend root');
  }
  return normalized;
}

export async function mes108ScreenshotFixture(
  { page },
  use,
  testInfo,
  { cwd = process.cwd() } = {},
) {
  const screenshotOutputs = new Map();
  const evidence = {
    async capture(path, options = {}) {
      const normalized = repositoryRelativePath(path, cwd);
      const result = await page.screenshot({ ...options, path: resolve(cwd, normalized) });
      screenshotOutputs.set(normalized, {
        path: normalized,
        sha256: createHash('sha256').update(result).digest('hex'),
      });
      return result;
    },
  };
  try {
    await use(evidence);
  } finally {
    await testInfo.attach('mes108-screenshot-outputs', {
      body: Buffer.from(
        JSON.stringify(
          [...screenshotOutputs.values()].sort((left, right) =>
            left.path.localeCompare(right.path),
          ),
        ),
      ),
      contentType: 'application/json',
    });
  }
}

export const MES108_SCREENSHOT_FIXTURE_OPTIONS = { auto: true };

const test = base.extend({
  mes108Screenshot: [mes108ScreenshotFixture, MES108_SCREENSHOT_FIXTURE_OPTIONS],
});

export { expect, test };
