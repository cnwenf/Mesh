/**
 * CommentCard 组件测试(comment-inbox.md §4.1):作者/徽标/正文/已删除占位/已编辑、
 * 解决·重开按钮、反应切换、就地编辑保存、深链高亮。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { useSettingsStore } from '../../../state/settingsStore';
import { getIssueByIdentifier } from '../../issues/api';
import { CommentCard, escapeHtml, ISSUE_LINK_RE } from '../CommentCard';
import type { CommentCardProps } from '../CommentCard';
import type { Comment } from '../types';

vi.mock('../../issues/api', () => ({
  getIssueByIdentifier: vi.fn(),
}));

const COMMENT: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: { id: 'mem-1', member_type: 'agent', name: 'reviewer' },
  body_markdown: 'hello',
  body_html: '<p>hello</p>',
  body_text: 'hello',
  reactions: [{ emoji: '👍', count: 2, reacted_by_me: false, actors: [] }],
  reply_count: 0,
  resolved_at: null,
  resolved_by: null,
  mentions: [],
  triggered_execution_ids: [],
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
  edited_at: null,
};

function renderCard(
  overrides: Partial<CommentCardProps> = {},
): ReturnType<typeof renderWithProviders> {
  const props: CommentCardProps = {
    comment: COMMENT,
    workspaceId: 'ws-1',
    locale: 'en',
    highlighted: false,
    canModify: true,
    onReply: vi.fn(),
    onResolve: vi.fn(),
    onReopen: vi.fn(),
    onToggleReaction: vi.fn(),
    onAddReaction: vi.fn(),
    onSaveEdit: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn(),
    onCopyLink: vi.fn(),
    ...overrides,
  };
  return renderWithProviders(<CommentCard {...props} />);
}

describe('CommentCard', () => {
  it('renders author, agent badge and sanitized body html', () => {
    renderCard();
    expect(screen.getByTestId('comment-author-c-1').textContent).toBe('reviewer');
    expect(screen.getByTestId('agent-badge')).toBeTruthy();
    expect(screen.getByTestId('comment-body-c-1').innerHTML).toContain('<p>hello</p>');
  });

  it('使用共享时间语义：自动刷新且 tooltip 保留本地时区与 UTC 原值', () => {
    const previousPreferences = useSettingsStore.getState().preferences;
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-01T00:03:10Z'));
    useSettingsStore.setState({
      preferences: { theme: null, locale: 'en', timezone: 'Asia/Shanghai' },
    });

    const view = renderCard();
    try {
      const time = screen.getByText('3 minutes ago');
      expect(time.tagName).toBe('TIME');
      expect(time).toHaveAttribute('datetime', COMMENT.created_at);
      expect(screen.getByRole('tooltip').textContent).toBe(
        '2026-07-01 08:00 (GMT+8) · UTC original: 2026-07-01T00:00:00Z',
      );

      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(screen.getByText('4 minutes ago')).toBeTruthy();
    } finally {
      view.unmount();
      useSettingsStore.setState({ preferences: previousPreferences });
      vi.useRealTimers();
    }
  });

  it('shows the deleted placeholder for deleted comments', () => {
    renderCard({ comment: { ...COMMENT, deleted_at: '2026-07-03T00:00:00Z', body_html: '' } });
    expect(screen.getByTestId('comment-deleted')).toBeTruthy();
    expect(screen.queryByTestId('comment-body-c-1')).toBeNull();
  });

  it('shows the edited marker when edited_at is set', () => {
    renderCard({ comment: { ...COMMENT, edited_at: '2026-07-02T06:00:00Z' } });
    expect(screen.getByTestId('comment-edited')).toBeTruthy();
  });

  it('offers resolve on an unresolved top-level thread and reopen when resolved', () => {
    renderCard();
    expect(screen.getByTestId('comment-resolve-c-1')).toBeTruthy();
    renderCard({ comment: { ...COMMENT, resolved_at: '2026-07-02T00:00:00Z' } });
    expect(screen.getByTestId('comment-reopen-c-1')).toBeTruthy();
  });

  it('does not show resolve/reopen for replies', () => {
    renderCard({ comment: { ...COMMENT, parent_id: 'c-0', thread_root_id: 'c-0' } });
    expect(screen.queryByTestId('comment-resolve-c-1')).toBeNull();
  });

  it('toggles a reaction', () => {
    const onToggle = vi.fn();
    renderCard({ onToggleReaction: onToggle });
    fireEvent.click(screen.getByTestId('reaction-👍'));
    expect(onToggle).toHaveBeenCalledWith(COMMENT, '👍');
  });

  it('edits in place and saves', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderCard({ onSaveEdit: onSave });
    fireEvent.click(screen.getByTestId('comment-edit-c-1'));
    const input = screen.getByTestId('comment-edit-input-c-1') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: 'updated body' } });
    fireEvent.click(screen.getByTestId('comment-edit-save-c-1'));
    expect(onSave).toHaveBeenCalledWith(COMMENT, 'updated body');
  });

  it('shows an edit error when saving fails and cancels edit', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('conflict'));
    renderCard({ onSaveEdit: onSave });
    fireEvent.click(screen.getByTestId('comment-edit-c-1'));
    fireEvent.change(screen.getByTestId('comment-edit-input-c-1'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('comment-edit-save-c-1'));
    await screen.findByTestId('comment-edit-error');
    // cancel returns to display mode
    fireEvent.click(screen.getByTestId('comment-edit-cancel-c-1'));
    expect(screen.queryByTestId('comment-edit-form-c-1')).toBeNull();
  });

  it('does not save an empty edit', () => {
    const onSave = vi.fn();
    renderCard({ onSaveEdit: onSave });
    fireEvent.click(screen.getByTestId('comment-edit-c-1'));
    fireEvent.change(screen.getByTestId('comment-edit-input-c-1'), { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('comment-edit-save-c-1'));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('invokes the reply handler', () => {
    const onReply = vi.fn();
    renderCard({ onReply });
    fireEvent.click(screen.getByTestId('comment-reply-c-1'));
    expect(onReply).toHaveBeenCalledWith(COMMENT);
  });

  it('adds a reaction from the picker', () => {
    const onAdd = vi.fn();
    renderCard({ onAddReaction: onAdd });
    fireEvent.click(screen.getByTestId('reaction-add'));
    fireEvent.click(screen.getByTestId('reaction-pick-🎉'));
    expect(onAdd).toHaveBeenCalledWith(COMMENT, '🎉');
  });

  it('hides edit/delete when the user cannot modify', () => {
    renderCard({ canModify: false });
    expect(screen.queryByTestId('comment-edit-c-1')).toBeNull();
    expect(screen.queryByTestId('comment-delete-c-1')).toBeNull();
  });

  it('copies the link', () => {
    const onCopy = vi.fn();
    renderCard({ onCopyLink: onCopy });
    fireEvent.click(screen.getByTestId('comment-copy-c-1'));
    expect(onCopy).toHaveBeenCalledWith(COMMENT);
  });

  it('exposes the same actions in the touch "more" menu and invokes handlers (§9.5.6)', () => {
    const onDelete = vi.fn();
    const onCopy = vi.fn();
    const onReply = vi.fn();
    const onResolve = vi.fn();
    renderCard({ onDelete, onCopyLink: onCopy, onReply, onResolve });
    const openMenu = (): void => {
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    };
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Reply' }));
    expect(onReply).toHaveBeenCalledWith(COMMENT);
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Resolve' }));
    expect(onResolve).toHaveBeenCalledWith(COMMENT);
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Copy link' }));
    expect(onCopy).toHaveBeenCalledWith(COMMENT);
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    expect(onDelete).toHaveBeenCalledWith(COMMENT);
  });

  it('opens the edit form from the touch menu', () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
    expect(screen.getByTestId('comment-edit-form-c-1')).toBeTruthy();
  });

  it('omits edit/delete from the touch menu when the user cannot modify', () => {
    renderCard({ canModify: false });
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByRole('menuitem', { name: 'Reply' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Delete' })).toBeNull();
  });

  it('escapeHtml escapes the injection-relevant chars', () => {
    const out = escapeHtml(`<img src=x onerror="alert(1)">&`);
    expect(out).not.toContain('<');
    expect(out).not.toContain('>');
    expect(out).toContain('&lt;');
    expect(out).toContain('&gt;');
    expect(out).toContain('&amp;');
    expect(out).toContain('&quot;');
  });

  it('ISSUE_LINK_RE matches the server-emitted anchor only', () => {
    const html =
      'see <a class="mesh-issue-link" data-issue-identifier="MES-123" ' +
      'href="/issues/by-identifier/MES-123">#MES-123</a> and <a href="https://x">y</a>';
    const ids = Array.from(html.matchAll(ISSUE_LINK_RE), (m) => m[1]);
    expect(ids).toEqual(['MES-123']);
  });

  it('hydrates #IDENTIFIER links into reference cards (C6)', async () => {
    vi.mocked(getIssueByIdentifier).mockResolvedValue({
      id: 'iss-9',
      title: 'Login <broken>',
      status: { name: 'In Progress' },
    } as never);
    renderCard({
      comment: {
        ...COMMENT,
        body_html:
          '<p>see <a class="mesh-issue-link" data-issue-identifier="MES-123" ' +
          'href="/issues/by-identifier/MES-123">#MES-123</a></p>',
      },
    });
    await waitFor(() => {
      expect(screen.getByTestId('comment-body-c-1').innerHTML).toContain('mesh-issue-card');
    });
    const html = screen.getByTestId('comment-body-c-1').innerHTML;
    expect(html).toContain('MES-123');
    expect(html).toContain('Login &lt;broken&gt;'); // title escaped
    expect(html).toContain('In Progress');
    expect(html).not.toContain('mesh-issue-link'); // anchor replaced
  });

  it('leaves the link intact when identifier fetch fails (C6 fallback)', async () => {
    vi.mocked(getIssueByIdentifier).mockRejectedValue(new Error('nope'));
    const linkHtml =
      '<p><a class="mesh-issue-link" data-issue-identifier="MES-1" ' +
      'href="/issues/by-identifier/MES-1">#MES-1</a></p>';
    renderCard({ comment: { ...COMMENT, body_html: linkHtml } });
    // give the rejected promise a tick, then assert the original anchor stays
    await waitFor(() => expect(vi.mocked(getIssueByIdentifier)).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByTestId('comment-body-c-1').innerHTML).toContain('mesh-issue-link');
    });
  });
});
