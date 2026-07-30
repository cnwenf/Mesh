import { useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../registry';
import type { ShortcutCommand } from '../registry';
import { CommandPalette } from '../CommandPalette';

const PALETTE_PROPS = {
  closeLabel: 'Close palette',
  searchPlaceholder: 'Search commands',
  emptyText: 'No matching commands',
  title: 'Command palette',
} as const;

const spies = {
  newIssue: vi.fn(),
  gotoBoard: vi.fn(),
  toggleTheme: vi.fn(),
};

function registerCommands(): void {
  const commands: ShortcutCommand[] = [
    { id: 'new-issue', label: 'Create issue', group: 'global', keywords: ['new', 'create'], run: spies.newIssue },
    { id: 'goto-board', label: 'Go to board', group: 'board', keywords: ['kanban'], run: spies.gotoBoard },
    { id: 'toggle-theme', label: 'Toggle theme', group: 'global', run: spies.toggleTheme },
  ];
  act(() => {
    for (const command of commands) useShortcutRegistry.getState().registerCommand(command);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  registerCommands();
});

describe('CommandPalette(Ctrl/Cmd+K 命令面板)', () => {
  it('open=false 时不渲染', () => {
    render(<CommandPalette open={false} onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开后:dialog 以 title 标注,搜索框聚焦,列出全部已注册命令(listbox/option)', () => {
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    const input = screen.getByRole('combobox');
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('placeholder', 'Search commands');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveTextContent('Create issue');
    // 默认选中第一项
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', options[0]?.id ?? '');
  });

  it('按 label 过滤', async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'board');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Go to board');
  });

  it('按 keywords 过滤', async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'kanban');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option')).toHaveTextContent('Go to board');
  });

  it('无匹配时展示 emptyText(prop),无 option', async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'zzz');
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
  });

  it('ArrowDown/ArrowUp 移动选择并循环', async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    await user.keyboard('{ArrowDown}');
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1]?.id ?? '');
    await user.keyboard('{ArrowUp}{ArrowUp}');
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');
  });

  it('Enter 执行选中命令并关闭', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');
    expect(spies.gotoBoard).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('过滤结果为空时 Enter 不执行也不关闭', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.type(screen.getByRole('combobox'), 'zzz');
    await user.keyboard('{Enter}');
    expect(onClose).not.toHaveBeenCalled();
    expect(spies.newIssue).not.toHaveBeenCalled();
  });

  it('点击选项执行命令并关闭(鼠标等价路径)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.click(screen.getByRole('option', { name: 'Toggle theme' }));
    expect(spies.toggleTheme).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Esc 关闭;关闭按钮(closeLabel)同样可用', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette open onClose={onClose} {...PALETTE_PROPS} />);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭后焦点归还触发元素;重新打开时查询清空', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Open palette
          </button>
          <CommandPalette open={open} onClose={() => setOpen(false)} {...PALETTE_PROPS} />
        </div>
      );
    }
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open palette' });
    await user.click(trigger);
    await user.type(screen.getByRole('combobox'), 'theme');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    await user.keyboard('{Escape}');
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    expect(screen.getByRole('combobox')).toHaveValue('');
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('无命令时直接展示 emptyText', () => {
    act(() => useShortcutRegistry.setState({ commands: [] }));
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('aria-controls 关联 listbox', () => {
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    const input = screen.getByRole('combobox');
    const listboxId = input.getAttribute('aria-controls') ?? '';
    expect(screen.getByRole('listbox')).toHaveAttribute('id', listboxId);
    expect(input).toHaveAttribute('aria-expanded', 'true');
  });

  it('initialQuery 提供时以该查询打开并按查询过滤(顶栏搜索续输入展开同一面板,search-command-palette S1)', () => {
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} initialQuery="theme" />);
    const input = screen.getByRole('combobox');
    expect(input).toHaveFocus();
    expect(input).toHaveValue('theme');
    expect(screen.getAllByRole('option')).toHaveLength(1);
  });

  it('initialQuery 缺省时打开仍清空查询(向后兼容)', () => {
    render(<CommandPalette open onClose={() => undefined} {...PALETTE_PROPS} />);
    expect(screen.getByRole('combobox')).toHaveValue('');
  });
});
