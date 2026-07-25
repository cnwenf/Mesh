import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../registry';
import type { ShortcutContext } from '../registry';
import { ShortcutHelp } from '../ShortcutHelp';

const GROUP_LABELS: Record<ShortcutContext, string> = {
  global: 'Global',
  board: 'Board',
  issue: 'Issue detail',
  chat: 'Chat',
};

const HELP_PROPS = {
  title: 'Keyboard shortcuts',
  closeLabel: 'Close help',
  groupLabels: GROUP_LABELS,
} as const;

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  act(() => {
    useShortcutRegistry.getState().registerShortcuts([
      { id: 'palette', combo: 'mod+k', label: 'Open command palette', group: 'global', run: vi.fn() },
      { id: 'help', combo: '?', label: 'Show shortcuts', group: 'global', run: vi.fn() },
      { id: 'new', combo: 'c', label: 'New issue', group: 'global', run: vi.fn() },
      { id: 'inbox', combo: 'g i', label: 'Go to inbox', group: 'global', run: vi.fn() },
      { id: 'move', combo: 'm', label: 'Move card', group: 'board', run: vi.fn() },
      { id: 'edit', combo: 'e', label: 'Edit issue', group: 'issue', run: vi.fn() },
    ]);
  });
});

describe('ShortcutHelp(? 帮助层,随上下文分组实时反映)', () => {
  it('open=false 时不渲染', () => {
    render(<ShortcutHelp open={false} onClose={() => undefined} isMac={false} {...HELP_PROPS} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('列出 global + 当前上下文分组,未激活上下文不出现', () => {
    act(() => useShortcutRegistry.getState().setContexts(['board']));
    render(<ShortcutHelp open onClose={() => undefined} isMac={false} {...HELP_PROPS} />);
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Global' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Board' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Issue detail' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Chat' })).not.toBeInTheDocument();
    expect(screen.getByText('New issue')).toBeInTheDocument();
    expect(screen.getByText('Move card')).toBeInTheDocument();
    expect(screen.queryByText('Edit issue')).not.toBeInTheDocument();
  });

  it('无快捷键的分组不渲染标题', () => {
    render(<ShortcutHelp open onClose={() => undefined} isMac={false} {...HELP_PROPS} />);
    expect(screen.queryByRole('heading', { name: 'Board' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Global' })).toBeInTheDocument();
  });

  it('组合键经 Kbd 渲染:mod+k → Ctrl+K(非 mac)/ Cmd+K(mac)', () => {
    const { rerender } = render(<ShortcutHelp open onClose={() => undefined} isMac={false} {...HELP_PROPS} />);
    const kbd = screen.getByText('Ctrl+K');
    expect(kbd.tagName).toBe('KBD');

    rerender(<ShortcutHelp open onClose={() => undefined} isMac {...HELP_PROPS} />);
    expect(screen.getByText('Cmd+K').tagName).toBe('KBD');
  });

  it('序列键 g i 渲染为多个按键帽', () => {
    render(<ShortcutHelp open onClose={() => undefined} isMac={false} {...HELP_PROPS} />);
    const item = screen.getByText('Go to inbox').closest('li');
    const kbds = within(item as HTMLElement).getAllByRole('generic').length;
    expect(kbds).toBeGreaterThanOrEqual(2);
    expect(within(item as HTMLElement).getByText('G')).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText('I')).toBeInTheDocument();
  });

  it('关闭按钮(closeLabel)与 Esc 均关闭(鼠标等价路径)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ShortcutHelp open onClose={onClose} isMac={false} {...HELP_PROPS} />);
    await user.click(screen.getByRole('button', { name: 'Close help' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
