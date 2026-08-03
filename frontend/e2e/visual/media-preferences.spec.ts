/** MES-128 full-core regression for reduced motion and increased contrast preferences. */
import { expect, test } from '@playwright/test';
import { PAGES, prepareVisualPage } from './visual-helpers';

test('all core pages honor prefers-reduced-motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await prepareVisualPage(page, 'light');
  for (const spec of Object.values(PAGES)) {
    await page.goto(spec.path);
    await spec.ready(page);
    const motion = await page.evaluate(() => {
      const toMilliseconds = (value: string): number =>
        value.endsWith('ms') ? Number.parseFloat(value) : Number.parseFloat(value) * 1000;
      let maxAnimationMs = 0;
      let maxTransitionMs = 0;
      for (const element of document.querySelectorAll<HTMLElement>('*')) {
        const style = getComputedStyle(element);
        for (const duration of style.animationDuration.split(',')) {
          maxAnimationMs = Math.max(maxAnimationMs, toMilliseconds(duration.trim()));
        }
        for (const duration of style.transitionDuration.split(',')) {
          maxTransitionMs = Math.max(maxTransitionMs, toMilliseconds(duration.trim()));
        }
      }
      return {
        matches: matchMedia('(prefers-reduced-motion: reduce)').matches,
        maxAnimationMs,
        maxTransitionMs,
      };
    });
    expect(motion.matches).toBe(true);
    expect(motion.maxAnimationMs).toBeLessThanOrEqual(0.01);
    expect(motion.maxTransitionMs).toBeLessThanOrEqual(0.01);
  }
});

test('all core pages honor prefers-contrast: more', async ({ page }) => {
  await page.emulateMedia({ contrast: 'more' });
  await prepareVisualPage(page, 'light');
  for (const spec of Object.values(PAGES)) {
    await page.goto(spec.path);
    await spec.ready(page);
    const contrast = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return {
        matches: matchMedia('(prefers-contrast: more)').matches,
        text: style.getPropertyValue('--color-text').trim(),
        muted: style.getPropertyValue('--color-text-muted').trim(),
        border: style.getPropertyValue('--color-border').trim(),
      };
    });
    expect(contrast.matches).toBe(true);
    expect(contrast.muted).toBe(contrast.text);
    expect(contrast.border).toBe(contrast.text);
  }
});
