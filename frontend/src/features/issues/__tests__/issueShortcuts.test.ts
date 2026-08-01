import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../../../shortcuts';
import { focusIssueProperty, registerIssueContextShortcuts } from '../issueShortcuts';

const labels = {
  edit: 'Edit issue',
  status: 'Change status',
  assignee: 'Change assignee',
  priority: 'Change priority',
  labels: 'Edit labels',
  milestone: 'Set milestone',
  submitComment: 'Submit comment',
  close: 'Close issue',
};

describe('registerIssueContextShortcuts(search-command-palette §4.3)', () => {
  beforeEach(() => {
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    document.body.innerHTML = `
      <input data-testid="issue-detail-title" />
      <select data-testid="issue-detail-status"></select>
      <select data-testid="issue-detail-assignee"></select>
      <select data-testid="issue-detail-priority"></select>
      <input data-testid="issue-label-search" />
      <select data-testid="issue-detail-milestone"></select>
      <button data-testid="composer-submit">Submit</button>
    `;
  });

  it('激活 issue context，完整注册 E/S/A/P/L/M/mod+Enter/Esc 并在清理时注销', () => {
    const close = vi.fn();
    const cleanup = registerIssueContextShortcuts({ labels, close });

    expect(useShortcutRegistry.getState().activeContexts).toEqual(['issue']);
    expect(
      useShortcutRegistry
        .getState()
        .shortcuts.filter((entry) => entry.group === 'issue')
        .map((entry) => entry.combo)
        .sort(),
    ).toEqual(['e', 's', 'a', 'p', 'l', 'm', 'mod+enter', 'esc'].sort());
    expect(
      useShortcutRegistry.getState().commands.filter((entry) => entry.group === 'issue'),
    ).toHaveLength(8);

    cleanup();
    expect(useShortcutRegistry.getState().activeContexts).toEqual([]);
    expect(
      useShortcutRegistry.getState().shortcuts.filter((entry) => entry.group === 'issue'),
    ).toEqual([]);
    expect(
      useShortcutRegistry.getState().commands.filter((entry) => entry.group === 'issue'),
    ).toEqual([]);
  });

  it.each([
    ['e', 'issue-detail-title'],
    ['s', 'issue-detail-status'],
    ['a', 'issue-detail-assignee'],
    ['p', 'issue-detail-priority'],
    ['l', 'issue-label-search'],
    ['m', 'issue-detail-milestone'],
  ])('%s 聚焦等价属性控件 %s', (combo, testId) => {
    const cleanup = registerIssueContextShortcuts({ labels, close: vi.fn() });
    useShortcutRegistry
      .getState()
      .shortcuts.find((entry) => entry.combo === combo)
      ?.run();
    expect(document.querySelector(`[data-testid="${testId}"]`)).toHaveFocus();
    cleanup();
  });

  it('mod+Enter 点击评论提交按钮，Esc 调用关闭动作', () => {
    const close = vi.fn();
    const click = vi.spyOn(
      document.querySelector<HTMLButtonElement>('[data-testid="composer-submit"]')!,
      'click',
    );
    const cleanup = registerIssueContextShortcuts({ labels, close });

    useShortcutRegistry
      .getState()
      .shortcuts.find((entry) => entry.combo === 'mod+enter')
      ?.run();
    useShortcutRegistry
      .getState()
      .shortcuts.find((entry) => entry.combo === 'esc')
      ?.run();

    expect(click).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('属性深链只聚焦已知目标，空值与未知值安全忽略', () => {
    expect(focusIssueProperty('status')).toBe(true);
    expect(document.querySelector('[data-testid="issue-detail-status"]')).toHaveFocus();
    expect(focusIssueProperty(null)).toBe(false);
    expect(focusIssueProperty('unknown')).toBe(false);
  });
});
