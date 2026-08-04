import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const loadStyles = (feature: string, file: string): string =>
  readFileSync(path.resolve(process.cwd(), `src/features/${feature}/${file}`), 'utf8');

const styles = {
  analytics: loadStyles('analytics', 'analytics.css'),
  approvals: loadStyles('approvals', 'approvals.css'),
  autopilots: loadStyles('autopilots', 'autopilots.css'),
  dataJobs: loadStyles('data-jobs', 'dataJobs.css'),
  integrations: loadStyles('integrations', 'integrations.css'),
  runtimes: loadStyles('runtimes', 'runtimes.css'),
  skills: loadStyles('skills', 'skills.css'),
  squads: loadStyles('squads', 'squads.css'),
} as const;

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function declarationBlock(source: string, selector: string): string {
  const match = source.match(new RegExp(`${escapeRegExp(selector)}\\s*\\{([^}]+)\\}`));
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? '';
}

describe('platform page visual-fidelity contract', () => {
  it.each([
    ['autopilots', '.mesh-autopilots__page'],
    ['integrations', '.mesh-integrations__page'],
    ['runtimes', '.mesh-runtimes'],
    ['skills', '.mesh-skills'],
    ['squads', '.mesh-squads'],
  ] as const)('%s uses the dense full-width workbench geometry', (feature, selector) => {
    const block = declarationBlock(styles[feature], selector);
    expect(block).toContain('container-type: inline-size;');
    expect(block).toContain('inline-size: 100%;');
    expect(block).toContain('max-inline-size: var(--content-wide);');
    expect(block).toContain('gap: var(--space-3);');
  });

  it.each([
    ['analytics', '.mesh-analytics__toolbar'],
    ['autopilots', '.mesh-autopilots__toolbar'],
    ['integrations', '.mesh-integrations__toolbar'],
    ['runtimes', '.mesh-runtimes__toolbar'],
    ['skills', '.mesh-skills__toolbar'],
    ['squads', '.mesh-squads__filters'],
  ] as const)('%s gives toolbars compact bounded chrome', (feature, selector) => {
    const block = declarationBlock(styles[feature], selector);
    expect(block).toContain('padding: var(--space-2);');
    expect(block).toContain('border: 1px solid var(--color-border-subtle);');
    expect(block).toContain('border-radius: var(--radius-lg);');
    expect(block).toContain('background: var(--color-surface-subtle);');
  });

  it.each([
    ['skills', '.mesh-skills__grid'],
    ['squads', '.mesh-squads__grid'],
    ['approvals', '.mesh-approvals__list'],
  ] as const)('%s presents operational collections as quiet row groups', (feature, selector) => {
    const block = declarationBlock(styles[feature], selector);
    expect(block).toContain('gap: 0;');
    expect(block).toContain('border: 1px solid var(--color-border-subtle);');
    expect(block).toContain('border-radius: var(--radius-lg);');
    expect(block).toContain('overflow: hidden;');
  });

  it.each([
    ['autopilots', '.mesh-autopilots__table'],
    ['dataJobs', '.mesh-data-jobs__table'],
    ['integrations', '.mesh-integrations__table'],
    ['runtimes', '.mesh-runtimes__table'],
  ] as const)('%s keeps desktop data in a bounded, dense table', (feature, selector) => {
    const block = declarationBlock(styles[feature], selector);
    expect(block).toContain('border-collapse: separate;');
    expect(block).toContain('border-spacing: 0;');
    expect(block).toContain('border: 1px solid var(--color-border-subtle);');
    expect(block).toContain('border-radius: var(--radius-lg);');
    expect(block).toContain('font-size: var(--font-size-body-sm);');
  });

  it.each(Object.entries(styles))(
    '%s explicitly calibrates raised surfaces for dark mode',
    (_, css) => {
      expect(css).toContain(":root[data-theme='dark']");
      expect(css).toContain('background: var(--color-surface-raised);');
      expect(css).toContain('border-color: var(--color-border-subtle);');
    },
  );

  it.each(Object.entries(styles))('%s uses the shared type and radius scales', (_, css) => {
    expect(css).not.toMatch(/font-size:\s*(?:0\.(?:72|75|78|8|82|85|88|9)|1\.(?:35|4))rem/);
    expect(css).not.toMatch(/border-radius:\s*(?:6|8|999)px/);
  });
});
