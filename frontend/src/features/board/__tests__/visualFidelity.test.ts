import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/board/board.css'), 'utf8');

describe('Board visual-fidelity contracts', () => {
  it('uses the full content frame with a horizontal view rail and measured column geometry', () => {
    expect(css).toMatch(/\.mesh-board\s*\{[\s\S]*?display:\s*flex[\s\S]*?flex-direction:\s*column/);
    expect(css).toMatch(
      /\.mesh-view-switcher__list\s*\{[\s\S]*?flex-direction:\s*row[\s\S]*?overflow-x:\s*auto/,
    );
    expect(css).toMatch(/\.mesh-board__columns\s*\{[\s\S]*?gap:\s*var\(--space-4\)/);
    expect(css).toMatch(
      /\.mesh-board__column\s*\{[\s\S]*?inline-size:\s*280px[\s\S]*?flex:\s*0 0 280px/,
    );
  });

  it('uses rounded, pale status surfaces with compact metadata and no decorative card lift', () => {
    expect(css).toMatch(
      /\.mesh-board__column\s*\{[\s\S]*?border:\s*1px solid var\(--color-border-subtle\)[\s\S]*?border-radius:\s*var\(--radius-xl\)/,
    );
    expect(css).toMatch(
      /\.mesh-board__column--in_progress\s*\{[\s\S]*?background:\s*var\(--color-warning-bg\)/,
    );
    expect(css).toMatch(
      /\.mesh-board__column--in_review[\s\S]*?background:\s*var\(--color-success-bg\)/,
    );
    expect(css).toMatch(
      /\.mesh-board__card-assignee\s*\{[\s\S]*?font-size:\s*var\(--font-size-caption\)/,
    );

    const hoverRule = css.match(/\.mesh-board__card:hover\s*\{([\s\S]*?)\}/)?.[1] ?? '';
    expect(hoverRule).not.toContain('box-shadow');
    expect(hoverRule).not.toContain('transform');
  });

  it('uses information-dense card typography with a two-line title and supporting metadata', () => {
    expect(css).toMatch(
      /(?:^|\n)\.mesh-board__card\s*\{[\s\S]*?min-block-size:\s*140px[\s\S]*?padding:\s*var\(--space-3\)/,
    );
    expect(css).toMatch(/\.mesh-board__card-title\s*\{[\s\S]*?-webkit-line-clamp:\s*2/);
    expect(css).toMatch(
      /\.mesh-board__card-description\s*\{[\s\S]*?font-size:\s*var\(--font-size-caption\)/,
    );
    expect(css).toMatch(/\.mesh-board__card-footer\s*\{/);
  });
});
