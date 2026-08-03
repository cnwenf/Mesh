/**
 * ShortcutHelp 附加操作区测试(onboarding.md §4.2 帮助菜单「重新显示上手清单」入口):
 * 仅在 restoreLabel + onRestore 同提供时渲染;点击触发回调。
 */
import { render, screen } from '@testing-library/react';
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

const BASE_PROPS = {
  title: 'Keyboard shortcuts',
  closeLabel: 'Close help',
  groupLabels: GROUP_LABELS,
} as const;

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
});

describe('ShortcutHelp restore section', () => {
  it('renders nothing extra when restore props are omitted', () => {
    render(<ShortcutHelp open onClose={() => undefined} isMac={false} {...BASE_PROPS} />);
    expect(screen.queryByTestId('help-restore-onboarding')).not.toBeInTheDocument();
  });

  it('renders nothing extra when only one restore prop is provided', () => {
    render(
      <ShortcutHelp
        open
        onClose={() => undefined}
        isMac={false}
        {...BASE_PROPS}
        restoreLabel="Show it again"
      />,
    );
    expect(screen.queryByTestId('help-restore-onboarding')).not.toBeInTheDocument();
  });

  it('renders the restore button and fires onRestore on click', async () => {
    const user = userEvent.setup();
    const onRestore = vi.fn();
    render(
      <ShortcutHelp
        open
        onClose={() => undefined}
        isMac={false}
        {...BASE_PROPS}
        restoreLabel="Show the getting-started checklist again"
        onRestore={onRestore}
      />,
    );
    const button = screen.getByTestId('help-restore-onboarding');
    expect(button).toHaveAttribute('data-slot', 'button');
    expect(button).toHaveTextContent('Show the getting-started checklist again');
    await user.click(button);
    expect(onRestore).toHaveBeenCalledTimes(1);
  });
});
