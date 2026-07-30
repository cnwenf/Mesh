import { useRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { focusableElements, trapTabKey, useFocusTrap } from '../components/useFocusTrap';

function TrapHarness({ open, restoreFocus = true }: { open: boolean; restoreFocus?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(open, ref, restoreFocus);
  if (!open) return null;
  return (
    <div ref={ref} tabIndex={-1} data-testid="trap">
      <button type="button">第一</button>
      <button type="button">第二</button>
    </div>
  );
}

describe('focusableElements', () => {
  it('按 DOM 顺序收集可聚焦元素,排除 disabled 与 tabindex=-1', () => {
    render(
      <div data-testid="root">
        <a href="/x">链接</a>
        <button type="button">按钮</button>
        <button type="button" disabled>禁用</button>
        <input aria-label="输入" />
        <div tabIndex={-1}>不可聚焦</div>
        <div tabIndex={0}>可聚焦块</div>
      </div>,
    );
    const root = screen.getByTestId('root');
    const focusables = focusableElements(root);
    expect(focusables).toHaveLength(4);
    expect(focusables.map((el) => el.textContent || el.getAttribute('aria-label'))).toEqual([
      '链接',
      '按钮',
      '输入',
      '可聚焦块',
    ]);
  });
});

describe('trapTabKey', () => {
  function setup(): HTMLElement {
    render(
      <div data-testid="root" tabIndex={-1}>
        <button type="button">第一</button>
        <button type="button">第二</button>
      </div>,
    );
    return screen.getByTestId('root');
  }

  it('末项 Tab → 回首项并返回 true', () => {
    const root = setup();
    screen.getByRole('button', { name: '第二' }).focus();
    expect(trapTabKey(root, false)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '第一' }));
  });

  it('首项 Shift+Tab → 回末项并返回 true', () => {
    const root = setup();
    screen.getByRole('button', { name: '第一' }).focus();
    expect(trapTabKey(root, true)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '第二' }));
  });

  it('焦点在容器自身时 Shift+Tab → 末项', () => {
    const root = setup();
    root.focus();
    expect(trapTabKey(root, true)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '第二' }));
  });

  it('中间位置不拦截(返回 false)', () => {
    const root = setup();
    screen.getByRole('button', { name: '第一' }).focus();
    expect(trapTabKey(root, false)).toBe(false);
  });

  it('无焦点元素时把焦点留在容器并返回 true', () => {
    render(
      <div data-testid="empty" tabIndex={-1}>
        <p>纯文本</p>
      </div>,
    );
    const root = screen.getByTestId('empty');
    expect(trapTabKey(root, false)).toBe(true);
    expect(document.activeElement).toBe(root);
  });
});

describe('useFocusTrap', () => {
  it('打开时焦点进入容器', () => {
    render(<TrapHarness open />);
    expect(screen.getByTestId('trap')).toHaveFocus();
  });

  it('关闭后归还焦点(默认)', async () => {
    const user = userEvent.setup();
    const outside = document.createElement('button');
    outside.textContent = '外部';
    document.body.appendChild(outside);
    await user.click(outside);
    const { unmount } = render(<TrapHarness open />);
    expect(screen.getByTestId('trap')).toHaveFocus();
    unmount();
    expect(outside).toHaveFocus();
    outside.remove();
  });

  it('restoreFocus=false 时不归还焦点', async () => {
    const user = userEvent.setup();
    const outside = document.createElement('button');
    outside.textContent = '外部2';
    document.body.appendChild(outside);
    await user.click(outside);
    const { unmount } = render(<TrapHarness open restoreFocus={false} />);
    unmount();
    expect(document.activeElement).not.toBe(outside);
    outside.remove();
  });

  it('open=false 不夺取焦点也不报错', () => {
    const before = document.activeElement;
    render(<TrapHarness open={false} />);
    expect(screen.queryByTestId('trap')).toBeNull();
    expect(document.activeElement).toBe(before);
  });

  it('重复打开关闭稳定(幂等)', () => {
    const { rerender } = render(<TrapHarness open={false} />);
    rerender(<TrapHarness open />);
    expect(screen.getByTestId('trap')).toHaveFocus();
    rerender(<TrapHarness open={false} />);
    rerender(<TrapHarness open />);
    expect(screen.getByTestId('trap')).toHaveFocus();
  });
});

// 触发 vi 引入(避免未使用告警,本文件断言以 DOM 为主)
void vi;
