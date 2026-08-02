/**
 * SeoMeta(§3.4 规则 5):认证内页面统一 noindex + canonical 规范深链,
 * 路由切换即更新(客户端 head 操纵,无 SSR)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, Link } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { SeoMeta } from '../SeoMeta';

function cleanupHead(): void {
  document.head.querySelector('meta[name="robots"]')?.remove();
  document.head.querySelector('link[rel="canonical"]')?.remove();
}

afterEach(cleanupHead);

describe('SeoMeta', () => {
  it('noindex + canonical 指向当前规范路径', () => {
    render(
      <MemoryRouter initialEntries={['/w/acme/board?view=x']}>
        <SeoMeta />
        <Routes>
          <Route path="*" element={<div />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(document.querySelector('meta[name="robots"]')?.getAttribute('content')).toBe('noindex');
    const canonical = document.querySelector('link[rel="canonical"]')?.getAttribute('href') ?? '';
    expect(canonical).toContain('/w/acme/board');
    expect(canonical).toContain('view=x');
    expect(canonical.startsWith(window.location.origin)).toBe(true);
  });

  it('路由切换 → canonical 幂等更新(不重复插入元素)', async () => {
    render(
      <MemoryRouter initialEntries={['/w/acme/board']}>
        <SeoMeta />
        <Routes>
          <Route
            path="/w/acme/board"
            element={<Link to="/w/acme/inbox" data-testid="go-inbox">inbox</Link>}
          />
          <Route path="/w/acme/inbox" element={<div data-testid="inbox-page" />} />
        </Routes>
      </MemoryRouter>,
    );
    const canonical = document.querySelector('link[rel="canonical"]');
    expect(canonical?.getAttribute('href')).toContain('/w/acme/board');

    fireEvent.click(screen.getByTestId('go-inbox'));
    await waitFor(() =>
      expect(document.querySelector('link[rel="canonical"]')?.getAttribute('href')).toContain(
        '/w/acme/inbox',
      ),
    );
    expect(document.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
    expect(document.querySelectorAll('meta[name="robots"]')).toHaveLength(1);
  });
});
