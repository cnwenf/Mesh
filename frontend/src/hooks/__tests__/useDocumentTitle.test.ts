import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useUnreadStore } from '../../state/unreadStore';
import { DEFAULT_PRODUCT_NAME, useDocumentTitle } from '../useDocumentTitle';

describe('useDocumentTitle(design-quality G19 路由级标签页标题)', () => {
  afterEach(() => {
    document.title = '';
    useUnreadStore.setState({ count: 0 });
  });

  it('写入「页面标题 · 产品名」格式', () => {
    renderHook(() => useDocumentTitle('Sign in'));
    expect(document.title).toBe(`Sign in · ${DEFAULT_PRODUCT_NAME}`);
  });

  it('标题为空时仅保留产品名', () => {
    renderHook(() => useDocumentTitle(''));
    expect(document.title).toBe(DEFAULT_PRODUCT_NAME);
  });

  it('标题为纯空白时视同空,仅保留产品名', () => {
    renderHook(() => useDocumentTitle('   '));
    expect(document.title).toBe(DEFAULT_PRODUCT_NAME);
  });

  it('标题变化时同步更新(实体标识异步解析后补题)', () => {
    const { rerender } = renderHook(({ title }: { title: string }) => useDocumentTitle(title), {
      initialProps: { title: 'Loading' },
    });
    expect(document.title).toBe(`Loading · ${DEFAULT_PRODUCT_NAME}`);
    rerender({ title: 'MES-123 修复登录' });
    expect(document.title).toBe(`MES-123 修复登录 · ${DEFAULT_PRODUCT_NAME}`);
  });

  it('支持自定义产品名后缀', () => {
    renderHook(() => useDocumentTitle('Inbox', 'Acme'));
    expect(document.title).toBe('Inbox · Acme');
  });

  it('卸载时复位为产品名,不残留上一标题', () => {
    const { unmount } = renderHook(() => useDocumentTitle('Settings'));
    expect(document.title).toBe(`Settings · ${DEFAULT_PRODUCT_NAME}`);
    unmount();
    expect(document.title).toBe(DEFAULT_PRODUCT_NAME);
  });

  // MES-189 L93:未读 → 标题前缀(与 favicon 徽标同源 unreadStore)。
  it('有未读通知时标题带 (N) 前缀', () => {
    useUnreadStore.setState({ count: 3 });
    renderHook(() => useDocumentTitle('Inbox'));
    expect(document.title).toBe(`(3) Inbox · ${DEFAULT_PRODUCT_NAME}`);
  });

  it('未读前缀在空标题页同样生效(仅产品名也带计数)', () => {
    useUnreadStore.setState({ count: 2 });
    renderHook(() => useDocumentTitle(''));
    expect(document.title).toBe(`(2) ${DEFAULT_PRODUCT_NAME}`);
  });

  it('未读数变化时标题即时更新', () => {
    renderHook(() => useDocumentTitle('Issues'));
    expect(document.title).toBe(`Issues · ${DEFAULT_PRODUCT_NAME}`);
    act(() => useUnreadStore.setState({ count: 5 }));
    expect(document.title).toBe(`(5) Issues · ${DEFAULT_PRODUCT_NAME}`);
    act(() => useUnreadStore.setState({ count: 0 }));
    expect(document.title).toBe(`Issues · ${DEFAULT_PRODUCT_NAME}`);
  });
});
