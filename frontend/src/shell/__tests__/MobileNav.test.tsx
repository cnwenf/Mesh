/**
 * MobileNav — 底部主导航契约(design-quality §4.3)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { MobileNav } from '../MobileNav';

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
});
