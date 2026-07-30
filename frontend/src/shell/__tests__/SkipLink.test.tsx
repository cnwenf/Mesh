/**
 * SkipLink — 锚点契约与可访问名(design-quality §10.2)。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MAIN_CONTENT_ID, SkipLink } from '../SkipLink';

describe('SkipLink', () => {
  it('渲染指向主内容锚点的链接,可访问名来自 label prop', () => {
    render(<SkipLink label="跳到主内容" />);
    const link = screen.getByRole('link', { name: '跳到主内容' });
    expect(link).toHaveAttribute('href', `#${MAIN_CONTENT_ID}`);
    expect(link).toHaveClass('mesh-skip-link');
  });

  it('锚点 id 常量为 mesh-main-content(与 AppShell main 锚点契约一致)', () => {
    expect(MAIN_CONTENT_ID).toBe('mesh-main-content');
  });
});
