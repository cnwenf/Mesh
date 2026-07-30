/**
 * MobileNav — 底部主导航契约(design-quality §4.3)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { MobileNav } from '../MobileNav';
import type { NavItemKey } from '../navigation';

function renderNav(route = '/', onOpenMore = vi.fn()): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<MobileNav onOpenMore={onOpenMore} />, { route });
}

describe('MobileNav(手机底部主导航)', () => {
  it('含五主入口:工作台/工作项/看板/聊天 + 更多', () => {
    renderNav('/');
    expect(screen.getByTestId('mobile-nav-home')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-nav-issues')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-nav-board')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-nav-chat')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-nav-more')).toBeInTheDocument();
  });

  it('当前页链接携带 aria-current=page(状态不止于颜色)', () => {
    renderNav('/board');
    expect(screen.getByTestId('mobile-nav-board')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('mobile-nav-home')).not.toHaveAttribute('aria-current');
  });

  it('「更多」触发 onOpenMore 回调', () => {
    const onOpenMore = vi.fn();
    renderNav('/', onOpenMore);
    fireEvent.click(screen.getByTestId('mobile-nav-more'));
    expect(onOpenMore).toHaveBeenCalledTimes(1);
  });

  it('入口键缺失时模块加载即报错(fail-fast,不做渲染期静默兜底)', async () => {
    vi.resetModules();
    vi.doMock('../navigation', async (importOriginal) => {
      const actual = await importOriginal<typeof import('../navigation')>();
      return {
        ...actual,
        // 故意注入导航源中不存在的入口键,触发导入期 fail-fast
        MOBILE_PRIMARY_KEYS: [...actual.MOBILE_PRIMARY_KEYS, 'ghost' as unknown as NavItemKey],
      };
    });
    try {
      await expect(import('../MobileNav')).rejects.toThrow('mobile nav entry missing: ghost');
    } finally {
      vi.doUnmock('../navigation');
      vi.resetModules();
    }
  });
});
