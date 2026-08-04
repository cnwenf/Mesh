import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/projects/projects.css'), 'utf8');

describe('Projects visual-fidelity contracts', () => {
  it('keeps the project index as a dense, neutral table instead of a decorative card grid', () => {
    expect(css).toMatch(
      /\.mesh-projects__table\s*\{[\s\S]*?border-collapse:\s*collapse[\s\S]*?font-size:\s*var\(--font-size-body-sm\)/,
    );
    expect(css).toMatch(
      /\.mesh-projects__row\s*\{[\s\S]*?block-size:\s*44px[\s\S]*?border-block-end:\s*1px solid var\(--color-border-subtle\)/,
    );
    expect(css).toMatch(
      /\.mesh-projects__table th,[\s\S]*?\.mesh-projects__cell\s*\{[\s\S]*?padding:\s*var\(--space-1-5\) var\(--space-3\)/,
    );
    expect(css).not.toMatch(/\.mesh-projects__card:hover\s*\{[\s\S]*?box-shadow:/);
  });
});
