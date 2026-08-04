import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/board/board.css'), 'utf8');

describe('Board visual-fidelity contracts', () => {
  it('uses rounded, pale status surfaces with compact metadata and no decorative card lift', () => {
    expect(css).toMatch(
      /\.mesh-board__column\s*\{[\s\S]*?border:\s*1px solid var\(--color-border-subtle\)[\s\S]*?border-radius:\s*var\(--radius-lg\)/,
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
});
