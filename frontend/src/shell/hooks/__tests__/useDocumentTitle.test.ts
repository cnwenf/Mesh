/**
 * useDocumentTitle 单测 — G19 动态标签页标题机制。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { APP_TITLE_SUFFIX, composeDocumentTitle, useDocumentTitle } from '../useDocumentTitle';

describe('composeDocumentTitle', () => {
  it('joins segments with the brand suffix', () => {
    expect(composeDocumentTitle(['MES-123', '修复登录'])).toBe(`MES-123 · 修复登录 · ${APP_TITLE_SUFFIX}`);
  });

  it('falls back to the app name when no valid parts', () => {
    expect(composeDocumentTitle([])).toBe(APP_TITLE_SUFFIX);
    expect(composeDocumentTitle([undefined, undefined])).toBe(APP_TITLE_SUFFIX);
    expect(composeDocumentTitle(['', '   '])).toBe(APP_TITLE_SUFFIX);
  });

  it('skips undefined / blank parts and trims whitespace', () => {
    expect(composeDocumentTitle([' 洞察 ', undefined, ''])).toBe(`洞察 · ${APP_TITLE_SUFFIX}`);
  });
});

describe('useDocumentTitle', () => {
  afterEach(() => {
    document.title = 'Mesh';
  });

  it('sets the composed title on mount', () => {
    renderHook(() => useDocumentTitle('审批'));
    expect(document.title).toBe(`审批 · ${APP_TITLE_SUFFIX}`);
  });

  it('updates the title when parts change (async data arrival)', () => {
    const { rerender } = renderHook(({ title }: { title: string | undefined }) => useDocumentTitle(title, '设置'), {
      initialProps: { title: undefined },
    });
    expect(document.title).toBe(`设置 · ${APP_TITLE_SUFFIX}`);

    rerender({ title: 'MES-127' });
    expect(document.title).toBe(`MES-127 · 设置 · ${APP_TITLE_SUFFIX}`);
  });

  it('restores the previous title on unmount', () => {
    document.title = 'original';
    const { unmount } = renderHook(() => useDocumentTitle('洞察'));
    expect(document.title).toBe(`洞察 · ${APP_TITLE_SUFFIX}`);

    unmount();
    expect(document.title).toBe('original');
  });

  it('nested pages restore in LIFO order', () => {
    document.title = 'base';
    const outer = renderHook(() => useDocumentTitle('设置'));
    const inner = renderHook(() => useDocumentTitle('外观'));
    expect(document.title).toBe(`外观 · ${APP_TITLE_SUFFIX}`);

    act(() => inner.unmount());
    expect(document.title).toBe(`设置 · ${APP_TITLE_SUFFIX}`);

    act(() => outer.unmount());
    expect(document.title).toBe('base');
  });
});
