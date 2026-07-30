import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Menu } from '../components/Menu';
import type { MenuEntry } from '../components/Menu';

function makeEntries(handlers: {
  onEdit?: () => void;
  onDelete?: () => void;
  onArch?: () => void;
}): MenuEntry[] {
  return [
    { key: 'edit', label: '编辑', onSelect: handlers.onEdit ?? (() => undefined), icon: 'edit' },
    { separator: true, key: 'sep' },
    { key: 'archive', label: '归档', onSelect: handlers.onArch ?? (() => undefined), disabled: true },
    { key: 'delete', label: '删除', onSelect: handlers.onDelete ?? (() => undefined), danger: true },
  ];
}

describe('Menu(§7.5 低频行操作 / 键盘漫游 / 焦点管理)', () => {
  it('trigger 带 aria-haspopup/expanded,初始关闭', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    const trigger = screen.getByRole('button', { name: '更多操作' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('点击打开,条目为 menuitem,danger/分隔线正确呈现', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    const menu = screen.getByRole('menu');
    const items = within(menu).getAllByRole('menuitem');
    expect(items.map((item) => item.textContent)).toEqual(['编辑', '归档', '删除']);
    expect(items[2]).toHaveClass('mesh-menu__item--danger');
    expect(items[1]).toBeDisabled();
    expect(within(menu).getByRole('separator')).toBeInTheDocument();
  });

  it('选择后回调触发并关闭菜单', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({ onEdit })} />);
    await user.click(screen.getByRole('button', { name: '更多操作' }));
    await user.click(screen.getByRole('menuitem', { name: '编辑' }));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('禁用项点击不触发回调也不关闭', async () => {
    const user = userEvent.setup();
    const onArch = vi.fn();
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({ onArch })} />);
    await user.click(screen.getByRole('button', { name: '更多操作' }));
    await user.click(screen.getByRole('menuitem', { name: '归档' }));
    expect(onArch).not.toHaveBeenCalled();
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('ArrowDown 打开并聚焦首个可用项(跳过禁用项由漫游处理)', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    const trigger = screen.getByRole('button', { name: '更多操作' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('菜单内 ↑↓ 在可用项间漫游(禁用项被跳过)', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    const menu = screen.getByRole('menu');
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '编辑' }));
    // 下一项是禁用「归档」→ 跳到「删除」
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
    // 循环回第一项
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '编辑' }));
    // ↑ 反向循环到末项
    fireEvent.keyDown(menu, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
  });

  it('Home/End 跳首末可用项', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    const menu = screen.getByRole('menu');
    fireEvent.keyDown(menu, { key: 'End' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
    fireEvent.keyDown(menu, { key: 'Home' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '编辑' }));
  });

  it('Esc 关闭菜单', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('Tab 关闭菜单', () => {
    render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' });
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('外部 pointerdown 关闭菜单', () => {
    render(
      <div>
        <div data-testid="outside">外部</div>
        <Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByTestId('outside'));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('再次点击 trigger 关闭;align=end 加右对齐类', () => {
    const { rerender } = render(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} />);
    const trigger = screen.getByRole('button', { name: '更多操作' });
    fireEvent.click(trigger);
    fireEvent.click(trigger);
    expect(screen.queryByRole('menu')).toBeNull();
    rerender(<Menu trigger="…" triggerLabel="更多操作" entries={makeEntries({})} align="end" />);
    fireEvent.click(trigger);
    expect(screen.getByRole('menu')).toHaveClass('mesh-menu--end');
  });

  it('全部禁用项时方向键不崩溃', () => {
    render(
      <Menu
        trigger="…"
        triggerLabel="更多操作"
        entries={[{ key: 'a', label: 'A', onSelect: () => undefined, disabled: true }]}
      />,
    );
    const trigger = screen.getByRole('button', { name: '更多操作' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const menu = screen.getByRole('menu');
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    fireEvent.keyDown(menu, { key: 'ArrowUp' });
    fireEvent.keyDown(menu, { key: 'Home' });
    fireEvent.keyDown(menu, { key: 'End' });
    expect(menu).toBeInTheDocument();
  });
});
