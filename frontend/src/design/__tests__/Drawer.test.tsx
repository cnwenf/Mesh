import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Drawer } from '../components/Drawer';

describe('Drawer(§7.5 次级上下文浮层)', () => {
  it('open=false 不渲染', () => {
    render(
      <Drawer open={false} onClose={() => undefined} title="属性">
        内容
      </Drawer>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('open=true:role=dialog + aria-modal + aria-label 标题', () => {
    render(
      <Drawer open onClose={() => undefined} title="工作项属性">
        内容
      </Drawer>,
    );
    const drawer = screen.getByRole('dialog', { name: '工作项属性' });
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(drawer).toHaveClass('mesh-drawer');
    expect(screen.getByText('工作项属性')).toHaveClass('mesh-drawer__title');
  });

  it('打开后焦点移入抽屉', () => {
    render(
      <Drawer open onClose={() => undefined} title="属性">
        内容
      </Drawer>,
    );
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('关闭后焦点归还触发元素', () => {
    const outside = document.createElement('button');
    outside.textContent = '打开抽屉';
    document.body.appendChild(outside);
    outside.focus();
    const { rerender } = render(
      <Drawer open onClose={() => undefined} title="属性">
        内容
      </Drawer>,
    );
    expect(screen.getByRole('dialog')).toHaveFocus();
    rerender(
      <Drawer open={false} onClose={() => undefined} title="属性">
        内容
      </Drawer>,
    );
    expect(outside).toHaveFocus();
    outside.remove();
  });

  it('Esc 触发 onClose', () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="属性">
        内容
      </Drawer>,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('点击遮罩关闭;点击内容不关闭', () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="属性">
        <p>抽屉内容</p>
      </Drawer>,
    );
    fireEvent.mouseDown(screen.getByText('抽屉内容'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(document.querySelector('.mesh-drawer__backdrop')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closeLabel 提供时渲染关闭按钮', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="属性" closeLabel="关闭抽屉">
        内容
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('footer 插槽渲染为底部操作区', () => {
    render(
      <Drawer open onClose={() => undefined} title="属性" footer={<button type="button">保存</button>}>
        内容
      </Drawer>,
    );
    expect(document.querySelector('.mesh-drawer__footer')).toContainElement(
      screen.getByRole('button', { name: '保存' }),
    );
  });

  it('Tab 在抽屉内循环(首项 Shift+Tab → 末项)', () => {
    render(
      <Drawer open onClose={() => undefined} title="属性" closeLabel="关闭">
        <button type="button">内部按钮</button>
      </Drawer>,
    );
    const first = screen.getByRole('button', { name: '关闭' });
    first.focus();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '内部按钮' }));
  });
});

describe('Drawer 其他按键不干扰(非 Escape/Tab 直接忽略)', () => {
  it('按下无关按键不关闭也不拦截', () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="属性">
        内容
      </Drawer>,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowDown' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

describe('Drawer Tab 中间位置不拦截(trapTabKey 返回 false 分支)', () => {
  it('非边缘焦点时 Tab 不阻止默认行为', () => {
    render(
      <Drawer open onClose={() => undefined} title="属性" closeLabel="关闭">
        <button type="button">中间按钮</button>
        <button type="button">末按钮</button>
      </Drawer>,
    );
    screen.getByRole('button', { name: '中间按钮' }).focus();
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    screen.getByRole('dialog').dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });
});

describe('Drawer 分支补强(焦点圈养边界)', () => {
  it('抽屉内无可聚焦元素时 Tab 被拦截且焦点留在面板', () => {
    render(
      <Drawer open onClose={() => undefined} title="纯文本抽屉">
        <p>没有可聚焦控件</p>
      </Drawer>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(dialog);
  });

  it('Shift+Tab 在面板自身聚焦时跳至末个可聚焦元素', () => {
    render(
      <Drawer open onClose={() => undefined} title="双控件抽屉">
        <button type="button">首个</button>
        <button type="button">末个</button>
      </Drawer>,
    );
    const dialog = screen.getByRole('dialog');
    dialog.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: '末个' })).toHaveFocus();
  });

  it('打开前焦点不在 HTMLElement 时关闭不抛错(activeElement 守卫)', () => {
    const { rerender } = render(
      <Drawer open onClose={() => undefined} title="守卫抽屉">
        <p>内容</p>
      </Drawer>,
    );
    // 模拟关闭前 activeElement 脱离 HTMLElement(body 仍为 HTMLElement,取 null 路径经 document 失焦)
    (document.activeElement as HTMLElement | null)?.blur?.();
    rerender(
      <Drawer open={false} onClose={() => undefined} title="守卫抽屉">
        <p>内容</p>
      </Drawer>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
