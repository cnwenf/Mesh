/**
 * useDocumentTitle(shell/hooks 变参语义段 API)单测 — G19 动态标签页标题机制。
 * 写入/复位语义统一经权威 hook(src/hooks/useDocumentTitle):「<语义段> · Mesh」,
 * 卸载复位产品名;本层仅负责多语义段组装(undefined/空白段过滤 + trim)。
 */
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { APP_TITLE_SUFFIX, composeDocumentTitle, useDocumentTitle } from '../useDocumentTitle';

describe('composeDocumentTitle', () => {
  it('joins semantic segments with the separator (brand suffix added by the base hook)', () => {
    expect(composeDocumentTitle(['MES-123', '修复登录'])).toBe('MES-123 · 修复登录');
  });

  it('returns empty string when no valid parts (base hook falls back to product name)', () => {
    expect(composeDocumentTitle([])).toBe('');
    expect(composeDocumentTitle([undefined, undefined])).toBe('');
    expect(composeDocumentTitle(['', '   '])).toBe('');
  });

  it('skips undefined / blank parts and trims whitespace', () => {
    expect(composeDocumentTitle([' 洞察 ', undefined, ''])).toBe('洞察');
  });
});

describe('useDocumentTitle', () => {
  afterEach(() => {
    document.title = 'Mesh';
  });

  it('sets a single-segment title with the brand suffix', () => {
    renderHook(() => useDocumentTitle('审批'));
    expect(document.title).toBe(`审批 · ${APP_TITLE_SUFFIX}`);
  });

  it('composes multiple segments (e.g. workspace name + page name)', () => {
    renderHook(() => useDocumentTitle('Acme', '设置'));
    expect(document.title).toBe(`Acme · 设置 · ${APP_TITLE_SUFFIX}`);
  });

  it('updates the title when segments change (async data arrival)', () => {
    const { rerender } = renderHook(
      ({ name }: { name: string | undefined }) => useDocumentTitle(name, '设置'),
      { initialProps: { name: undefined } as { name: string | undefined } },
    );
    // 名称未就绪:仅页面段(平滑降级,无「undefined」残留)
    expect(document.title).toBe(`设置 · ${APP_TITLE_SUFFIX}`);

    rerender({ name: 'Acme' });
    expect(document.title).toBe(`Acme · 设置 · ${APP_TITLE_SUFFIX}`);
  });

  it('falls back to the product name when all segments are empty', () => {
    renderHook(() => useDocumentTitle(undefined, '   '));
    expect(document.title).toBe(APP_TITLE_SUFFIX);
  });

  it('resets to the product name on unmount (base-hook contract)', () => {
    const { unmount } = renderHook(() => useDocumentTitle('洞察'));
    expect(document.title).toBe(`洞察 · ${APP_TITLE_SUFFIX}`);

    unmount();
    expect(document.title).toBe(APP_TITLE_SUFFIX);
  });
});
