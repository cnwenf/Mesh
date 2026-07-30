/**
 * MobileMoreDrawer — 「更多」导航抽屉契约(design-quality §4.3)。
 */
import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { MobileMoreDrawer } from '../MobileMoreDrawer';

function renderDrawer(open = true, onClose = vi.fn()): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<MobileMoreDrawer open={open} onClose={onClose} />);
}

describe('MobileMoreDrawer(「更多」导航抽屉)', () => {
  it('open=false 时不渲染', () => {
    renderDrawer(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开时以 dialog 呈现全部次级导航入口', () => {
    renderDrawer();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    for (const key of [
      'inbox',
      'projects',
      'members',
      'skills',
      'squads',
      'cycles',
      'autopilots',
      'automation',
      'insights',
      'integrations',
      'settings',
    ]) {
      expect(screen.getByTestId('mobile-drawer-nav-' + key)).toBeInTheDocument();
    }
  });

  it('Esc 触发 onClose', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭按钮触发 onClose(鼠标/触控等价路径)', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.click(screen.getByTestId('mobile-drawer-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('点击导航项后关闭抽屉(进入目标页由路由负责)', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.click(screen.getByTestId('mobile-drawer-nav-members'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Tab 在抽屉内循环(末项 Tab → 首项;首项 Shift+Tab → 末项)', async () => {
    renderDrawer();
    const dialog = screen.getByRole('dialog');
    const close = screen.getByTestId('mobile-drawer-close');
    const lastLink = screen.getByTestId('mobile-drawer-nav-settings');
    // 末项 Tab → 回首项(关闭按钮为面板首个可聚焦元素)
    lastLink.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(close).toHaveFocus();
    // 首项 Shift+Tab → 回末项
    close.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(lastLink).toHaveFocus();
    // 面板自身聚焦时 Shift+Tab 同样跳末项;非 Tab 键不干预
    dialog.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(lastLink).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'a' });
    expect(lastLink).toHaveFocus();
  });

  it('打开后焦点进入抽屉;关闭后归还触发元素', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Open drawer
          </button>
          <MobileMoreDrawer open={open} onClose={() => setOpen(false)} />
        </div>
      );
    }
    renderWithProviders(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open drawer' });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
